from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .models import Draft, SourceItem, now_iso

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
DEFAULT_ACCOUNT_ID = 1
DEFAULT_ACCOUNT_SLUG = "ai-signals"
DEFAULT_ACCOUNT_NAME = "AI 情报站"


def get_timezone(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("unknown timezone %s, falling back to the system timezone", name)
        return datetime.now().astimezone().tzinfo or timezone.utc


def start_of_local_day_iso(name: str) -> str:
    tz = get_timezone(name)
    now = datetime.now(tz)
    start = datetime.combine(now.date(), time.min, tzinfo=tz)
    return start.astimezone(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        if self.path.exists():
            with self.connect() as conn:
                tables = self._tables(conn)
                needs_migration = (
                    "source_items" in tables
                    and "account_id" not in self._columns(conn, "source_items")
                )
            if needs_migration:
                backup = self._backup()
                log.warning("migrating %s to schema v2; backup at %s", self.path, backup)

        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=OFF")
            tables = self._tables(conn)
            if "source_items" not in tables:
                self._create_schema(conn)
            elif "account_id" not in self._columns(conn, "source_items"):
                self._migrate_v1(conn)
            else:
                self._ensure_schema(conn)
            self._ensure_default_account(conn)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> set[str]:
        return {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _backup(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        target = self.path.with_name(f"{self.path.name}.bak-v1-{stamp}")
        with self.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        return target

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL DEFAULT 1,
                platform TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
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
                fetched_at TEXT NOT NULL,
                UNIQUE(account_id, source_url)
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL DEFAULT 1,
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
            CREATE TABLE IF NOT EXISTS post_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                path TEXT NOT NULL,
                source_url TEXT,
                width INTEGER,
                height INTEGER,
                bytes INTEGER,
                prepared INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL DEFAULT 1,
                trigger TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                fetched INTEGER NOT NULL DEFAULT 0,
                created INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                quota_reached INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
            CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at);
            CREATE INDEX IF NOT EXISTS idx_posts_account_status ON posts(account_id, status);
            CREATE INDEX IF NOT EXISTS idx_source_items_account ON source_items(account_id);
            CREATE INDEX IF NOT EXISTS idx_runs_finished_at ON runs(finished_at);
            CREATE INDEX IF NOT EXISTS idx_runs_account ON runs(account_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_source_content_hash
                ON source_items(account_id, content_hash) WHERE content_hash IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media(post_id, position);
            """
        )

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if "accounts" not in self._tables(conn):
            conn.execute(
                """CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
        if "post_media" not in self._tables(conn):
            conn.execute(
                """CREATE TABLE post_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    source_url TEXT,
                    width INTEGER,
                    height INTEGER,
                    bytes INTEGER,
                    prepared INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
                )"""
            )
        for table in ("posts", "runs"):
            if "account_id" not in self._columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN account_id INTEGER NOT NULL DEFAULT 1")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_account_status ON posts(account_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_source_items_account ON source_items(account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_account ON runs(account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media(post_id, position)")

    def _migrate_v1(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            ALTER TABLE posts RENAME TO posts_v1;
            ALTER TABLE source_items RENAME TO source_items_v1;
            ALTER TABLE runs RENAME TO runs_v1;
            DROP INDEX IF EXISTS idx_source_content_hash;
            DROP INDEX IF EXISTS idx_posts_status;
            DROP INDEX IF EXISTS idx_posts_created_at;
            DROP INDEX IF EXISTS idx_runs_finished_at;
            """
        )
        self._create_schema(conn)
        conn.executescript(
            """
            INSERT OR IGNORE INTO accounts (id, slug, name, created_at)
            VALUES (1, 'ai-signals', 'AI 情报站', datetime('now'));

            INSERT INTO source_items
            (id, account_id, platform, source_id, source_url, author, title, text, published_at,
             media_url, media_type, rights_status, heat_score, heat_label, ai_score,
             content_hash, raw_json, fetched_at)
            SELECT id, 1, platform, source_id, source_url, author, title, text, published_at,
                   media_url, media_type, rights_status, heat_score, heat_label, ai_score,
                   content_hash, raw_json, fetched_at
            FROM source_items_v1;

            INSERT INTO posts
            (id, account_id, source_item_id, title, body, tags_json, source_note, risk_flags_json,
             media_path, status, created_at, updated_at, published_at)
            SELECT id, 1, source_item_id, title, body, tags_json, source_note, risk_flags_json,
                   media_path, status, created_at, updated_at, published_at
            FROM posts_v1;

            INSERT INTO post_media (post_id, position, path, prepared)
            SELECT id, 0, media_path, 0 FROM posts WHERE media_path IS NOT NULL AND media_path != '';

            INSERT INTO runs
            (id, account_id, trigger, started_at, finished_at, fetched, created, skipped,
             quota_reached, errors_json)
            SELECT id, 1, trigger, started_at, finished_at, fetched, created, skipped,
                   quota_reached, errors_json
            FROM runs_v1;

            DROP TABLE posts_v1;
            DROP TABLE source_items_v1;
            DROP TABLE runs_v1;
            """
        )
        self._reset_sequences(conn)

    @staticmethod
    def _reset_sequences(conn: sqlite3.Connection) -> None:
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('source_items','posts','runs','post_media','accounts')")
            for table in ("source_items", "posts", "runs", "post_media", "accounts"):
                conn.execute(
                    f"INSERT INTO sqlite_sequence(name, seq) SELECT '{table}', COALESCE(MAX(id), 0) FROM {table}"
                )
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def _ensure_default_account(conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT id FROM accounts ORDER BY id LIMIT 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO accounts (slug, name, created_at) VALUES (?, ?, ?)",
                (DEFAULT_ACCOUNT_SLUG, DEFAULT_ACCOUNT_NAME, now_iso()),
            )

    def sync_accounts(self, accounts: list[Any]) -> dict[str, int]:
        output: dict[str, int] = {}
        with self.connect() as conn:
            for account in accounts:
                conn.execute(
                    """INSERT INTO accounts (slug, name, created_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(slug) DO UPDATE SET name=excluded.name""",
                    (account.slug, account.name, now_iso()),
                )
                row = conn.execute("SELECT id FROM accounts WHERE slug=?", (account.slug,)).fetchone()
                if row:
                    output[account.slug] = int(row["id"])
        return output

    def account_id(self, slug: str) -> Optional[int]:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM accounts WHERE slug=?", (slug,)).fetchone()
        return int(row["id"]) if row else None

    def insert_source(self, item: SourceItem, account_id: int = DEFAULT_ACCOUNT_ID) -> Optional[int]:
        digest = self.content_hash(item)
        metadata = dict(item.metadata)
        images = item.image_urls
        if images:
            metadata.setdefault("media_urls", images)
        with self.connect() as conn:
            if conn.execute(
                """SELECT 1 FROM source_items
                   WHERE account_id=? AND (source_url=? OR content_hash=?) LIMIT 1""",
                (account_id, item.source_url, digest),
            ).fetchone():
                return None
            try:
                cur = conn.execute(
                    """INSERT INTO source_items
                    (account_id, platform, source_id, source_url, author, title, text, published_at,
                     media_url, media_type, rights_status, heat_score, heat_label, ai_score,
                     content_hash, raw_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (account_id, item.platform, item.source_id, item.source_url, item.author,
                     item.title, item.text, item.published_at, item.media_url, item.media_type,
                     item.rights_status, item.heat_score, item.heat_label, item.ai_score,
                     digest, json.dumps(metadata, ensure_ascii=False, default=str), now_iso()),
                )
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def source_exists(self, item: SourceItem, account_id: int = DEFAULT_ACCOUNT_ID) -> bool:
        digest = self.content_hash(item)
        with self.connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM source_items
                   WHERE account_id=? AND (source_url=? OR content_hash=?) LIMIT 1""",
                (account_id, item.source_url, digest),
            ).fetchone()
        return row is not None

    def get_source_item(self, source_item_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM source_items WHERE id=?", (source_item_id,)).fetchone()
        return dict(row) if row else None

    def insert_post(self, source_item_id: int, draft: Draft,
                    media_paths: Optional[list[str]] = None,
                    media_path: Optional[str] = None,
                    account_id: int = DEFAULT_ACCOUNT_ID) -> Optional[int]:
        paths = [path for path in (media_paths or ([media_path] if media_path else [])) if path]
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO posts
                (account_id, source_item_id, title, body, tags_json, source_note, risk_flags_json,
                 media_path, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (account_id, source_item_id, draft.title, draft.body,
                 json.dumps(draft.tags, ensure_ascii=False), draft.source_note,
                 json.dumps(draft.risk_flags, ensure_ascii=False),
                 paths[0] if paths else None, now_iso(), now_iso()),
            )
            if not cur.rowcount:
                return None
            post_id = int(cur.lastrowid)
            self._insert_media(conn, post_id, paths)
            return post_id

    @staticmethod
    def _insert_media(conn: sqlite3.Connection, post_id: int, paths: list[str],
                      prepared: bool = False) -> None:
        for position, path in enumerate(paths):
            width = height = size = None
            try:
                size = Path(path).stat().st_size
                from PIL import Image
                with Image.open(path) as image:
                    width, height = image.size
            except Exception:
                pass
            conn.execute(
                """INSERT INTO post_media (post_id, position, path, width, height, bytes, prepared)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (post_id, position, path, width, height, size, int(prepared)),
            )

    def list_posts(self, status: Optional[str] = None, limit: int = 100,
                   ai_only: bool = False, min_ai_score: float = 0.0,
                   statuses: Optional[tuple[str, ...]] = None,
                   account_id: int = DEFAULT_ACCOUNT_ID) -> list[dict[str, Any]]:
        clauses: list[str] = ["p.account_id=?"]
        params: list[Any] = [account_id]
        if status:
            clauses.append("p.status=?")
            params.append(status)
        if statuses:
            clauses.append(f"p.status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if ai_only:
            clauses.append("s.ai_score>=?")
            params.append(min_ai_score)
        query = """SELECT p.*, a.slug AS account_slug, s.source_url, s.platform, s.author,
                          s.media_url, s.rights_status, s.heat_score, s.heat_label, s.ai_score
                   FROM posts p
                   JOIN source_items s ON s.id=p.source_item_id
                   JOIN accounts a ON a.id=p.account_id
                   WHERE """ + " AND ".join(clauses)
        query += " ORDER BY s.heat_score DESC, p.id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return self._rows_with_media(rows)

    def get_post(self, post_id: int) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT p.*, a.slug AS account_slug, s.source_url, s.platform, s.author,
                          s.media_url, s.rights_status, s.heat_score, s.heat_label, s.ai_score
                   FROM posts p
                   JOIN source_items s ON s.id=p.source_item_id
                   JOIN accounts a ON a.id=p.account_id
                   WHERE p.id=?""",
                (post_id,),
            ).fetchone()
        rows = self._rows_with_media([row] if row else [])
        return rows[0] if rows else None

    def _rows_with_media(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        data = [dict(row) for row in rows]
        if not data:
            return []
        ids = [int(item["id"]) for item in data]
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            media_rows = conn.execute(
                f"""SELECT post_id, position, path FROM post_media
                    WHERE post_id IN ({placeholders}) ORDER BY position""",
                ids,
            ).fetchall()
        by_post: dict[int, list[str]] = {}
        for row in media_rows:
            by_post.setdefault(int(row["post_id"]), []).append(str(row["path"]))
        output: list[dict[str, Any]] = []
        for item in data:
            paths = by_post.get(int(item["id"]), [])
            if not paths and item.get("media_path"):
                paths = [str(item["media_path"])]
            item["media_paths"] = paths
            item["media_files"] = [Path(path).name for path in paths]
            item["media_file"] = item["media_files"][0] if item["media_files"] else None
            item["media_count"] = len(paths)
            output.append(self._row(item))
        return output

    def update_status(self, post_id: int, status: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE posts SET status=?, updated_at=? WHERE id=?",
                (status, now_iso(), post_id),
            )
            return bool(cur.rowcount)

    def update_post(self, post_id: int, title: Optional[str] = None,
                    body: Optional[str] = None, tags: Optional[list[str]] = None,
                    risk_flags: Optional[list[str]] = None) -> bool:
        assignments = ["updated_at=?"]
        params: list[Any] = [now_iso()]
        if title is not None:
            assignments.append("title=?")
            params.append(title)
        if body is not None:
            assignments.append("body=?")
            params.append(body)
        if tags is not None:
            assignments.append("tags_json=?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if risk_flags is not None:
            assignments.append("risk_flags_json=?")
            params.append(json.dumps(risk_flags, ensure_ascii=False))
        params.append(post_id)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE posts SET {', '.join(assignments)} WHERE id=?", params
            )
            return bool(cur.rowcount)

    def replace_post_media(self, post_id: int, media_paths: Optional[list[str]],
                           prepared: bool = True) -> bool:
        paths = [path for path in (media_paths or []) if path]
        with self.connect() as conn:
            conn.execute("DELETE FROM post_media WHERE post_id=?", (post_id,))
            self._insert_media(conn, post_id, paths, prepared=prepared)
            cur = conn.execute(
                "UPDATE posts SET media_path=?, updated_at=? WHERE id=?",
                (paths[0] if paths else None, now_iso(), post_id),
            )
            return bool(cur.rowcount)

    def update_media(self, post_id: int, media_path: Optional[str]) -> bool:
        return self.replace_post_media(post_id, [media_path] if media_path else [])

    def mark_published(self, post_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE posts SET status='published', published_at=?, updated_at=? WHERE id=?",
                (now_iso(), now_iso(), post_id),
            )

    def count_posts_since(self, start_iso: str, account_id: int = DEFAULT_ACCOUNT_ID) -> int:
        with self.connect() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM posts WHERE account_id=? AND created_at>=?",
                (account_id, start_iso),
            ).fetchone()[0])

    def record_run(self, trigger: str, started_at: str, finished_at: str, fetched: int,
                   created: int, skipped: int, quota_reached: bool,
                   errors: list[str], account_id: int = DEFAULT_ACCOUNT_ID) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO runs
                (account_id, trigger, started_at, finished_at, fetched, created, skipped,
                 quota_reached, errors_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (account_id, trigger, started_at, finished_at, fetched, created, skipped,
                 int(quota_reached), json.dumps(errors, ensure_ascii=False)),
            )

    def stats(self, timezone_name: str = "Asia/Shanghai",
              ai_only: bool = False, min_ai_score: float = 0.0, target: int = 30,
              account_id: int = DEFAULT_ACCOUNT_ID) -> dict[str, Any]:
        tz = get_timezone(timezone_name)
        now_local = datetime.now(tz)
        day_start = start_of_local_day_iso(timezone_name)
        hour_floor = now_local.replace(minute=0, second=0, microsecond=0)
        hours = [hour_floor - timedelta(hours=11 - i) for i in range(12)]
        hour_start = hours[0].astimezone(timezone.utc).isoformat()

        ai_clause = " AND s.ai_score>=?" if ai_only else ""
        ai_params: list[Any] = [min_ai_score] if ai_only else []
        base_params: list[Any] = [account_id, *ai_params]

        with self.connect() as conn:
            status_rows = conn.execute(
                """SELECT p.status AS status, COUNT(*) AS count
                   FROM posts p JOIN source_items s ON s.id=p.source_item_id
                   WHERE p.account_id=?""" + ai_clause + " GROUP BY p.status",
                base_params,
            ).fetchall()
            status_counts = {row["status"]: int(row["count"]) for row in status_rows}

            platform_rows = conn.execute(
                """SELECT s.platform AS platform, COUNT(*) AS count
                   FROM posts p JOIN source_items s ON s.id=p.source_item_id
                   WHERE p.account_id=?""" + ai_clause + " GROUP BY s.platform",
                base_params,
            ).fetchall()
            by_platform = {row["platform"]: int(row["count"]) for row in platform_rows}

            today_rows = conn.execute(
                """SELECT p.created_at, p.published_at, s.platform, s.ai_score
                   FROM posts p JOIN source_items s ON s.id=p.source_item_id
                   WHERE p.account_id=? AND p.created_at>=?""" + ai_clause,
                [account_id, day_start, *ai_params],
            ).fetchall()

            hourly_rows = conn.execute(
                """SELECT p.created_at FROM posts p JOIN source_items s ON s.id=p.source_item_id
                   WHERE p.account_id=? AND p.created_at>=?""" + ai_clause,
                [account_id, hour_start, *ai_params],
            ).fetchall()

            run_row = conn.execute(
                "SELECT * FROM runs WHERE account_id=? ORDER BY id DESC LIMIT 1", (account_id,)
            ).fetchone()

        today_by_platform: Counter[str] = Counter()
        ai_scores: list[float] = []
        published_today = 0
        for row in today_rows:
            today_by_platform[row["platform"]] += 1
            ai_scores.append(float(row["ai_score"] or 0))
            if row["published_at"] and row["published_at"] >= day_start:
                published_today += 1

        hour_counts = {hour: 0 for hour in hours}
        for row in hourly_rows:
            try:
                created = datetime.fromisoformat(row["created_at"]).astimezone(tz)
            except (TypeError, ValueError):
                continue
            bucket = created.replace(minute=0, second=0, microsecond=0)
            if bucket in hour_counts:
                hour_counts[bucket] += 1

        peak_platform = max(today_by_platform, key=today_by_platform.get) if today_by_platform else None
        last_run = dict(run_row) if run_row else None
        if last_run:
            last_run["errors"] = json.loads(last_run.pop("errors_json") or "[]")
            last_run["quota_reached"] = bool(last_run["quota_reached"])

        return {
            "account_id": account_id,
            "target": target,
            "captured_today": len(today_rows),
            "published_today": published_today,
            "drafts": status_counts.get("draft", 0),
            "approved": status_counts.get("approved", 0),
            "published": status_counts.get("published", 0),
            "rejected": status_counts.get("rejected", 0),
            "total": sum(status_counts.values()),
            "avg_ai_score": round(sum(ai_scores) / len(ai_scores), 2) if ai_scores else 0.0,
            "by_platform": by_platform,
            "today_by_platform": dict(today_by_platform),
            "peak_platform": peak_platform,
            "hourly": [
                {"hour": hour.strftime("%H:00"), "count": hour_counts[hour]}
                for hour in hours
            ],
            "last_run": last_run,
        }

    @staticmethod
    def hash_text(title: str, text: str) -> str:
        normalized = re.sub(r"\s+", " ", f"{title or ''}\n{text or ''}").strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @classmethod
    def content_hash(cls, item: SourceItem) -> str:
        return cls.hash_text(item.title, item.text)

    @staticmethod
    def _row(data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        data["tags"] = json.loads(data.pop("tags_json"))
        if "risk_flags_json" in data:
            data["risk_flags"] = json.loads(data.pop("risk_flags_json"))
        return data