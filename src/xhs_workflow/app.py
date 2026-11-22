from __future__ import annotations

import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .accounts import AccountConfig, legacy_account
from .config import Settings
from .db import Database
from .publish_service import PublishService
from .publisher import Publisher, build_publisher, render_markdown
from .runtime import AccountRuntime
from .schedule import ScheduleStore, time_to_cron, validate_cron

log = logging.getLogger(__name__)


class PostUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=160)
    body: Optional[str] = Field(default=None, max_length=20000)
    tags: Optional[list[str]] = Field(default=None, max_length=20)


class ScheduleUpdate(BaseModel):
    collect_time: Optional[str] = None
    auto_publish_time: Optional[str] = None
    auto_publish_limit: Optional[int] = Field(default=None, ge=1, le=10)


def _publisher_ready(account: AccountConfig) -> bool:
    publish = account.publish
    if publish.mode == "manual":
        return True
    if publish.mode == "webhook":
        return bool(publish.resolved_webhook_url())
    if publish.mode == "mcp":
        return bool(publish.mcp_url)
    return False


def _coerce_runtimes(app_settings: Settings, db: Database, runtimes: Any,
                     publisher: Optional[Publisher]) -> list[AccountRuntime]:
    if isinstance(runtimes, (list, tuple)):
        return list(runtimes)
    account = legacy_account(app_settings)
    ids = db.sync_accounts([account])
    account.id = ids.get(account.slug, account.id)
    pub = publisher or build_publisher(app_settings, account)
    service = PublishService(app_settings, db, pub, account)
    return [AccountRuntime(account=account, pipeline=runtimes, publish_service=service)]


def create_app(app_settings: Settings, db: Database, runtimes: Any,
               publisher: Optional[Publisher] = None,
               enable_scheduler: bool = True) -> FastAPI:
    app_settings.media_dir.mkdir(parents=True, exist_ok=True)
    app_settings.export_dir.mkdir(parents=True, exist_ok=True)
    runtimes = _coerce_runtimes(app_settings, db, runtimes, publisher)
    if not runtimes:
        raise RuntimeError("no enabled accounts configured")
    registry: dict[str, AccountRuntime] = {rt.account.slug: rt for rt in runtimes}
    default_slug = runtimes[0].account.slug

    scheduler = BackgroundScheduler(timezone=app_settings.app_timezone)
    schedule_store = ScheduleStore(
        app_settings.database_path.parent / "schedule.json",
        app_settings,
        [rt.account for rt in runtimes],
    )
    for runtime in runtimes:
        runtime.publish_service.auto_publish_limit = schedule_store.auto_publish_limit(
            runtime.account.slug
        )

    def scheduled_collect(slug: str) -> None:
        runtime = registry.get(slug)
        if runtime:
            runtime.pipeline.run_once(trigger="scheduled")

    def scheduled_publish(slug: str) -> None:
        runtime = registry.get(slug)
        if runtime:
            runtime.publish_service.run_auto()

    def job_next_run_at(job_id: str) -> Optional[str]:
        if not scheduler.running:
            return None
        job = scheduler.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
        return None

    def next_run_at(slug: str) -> Optional[str]:
        return job_next_run_at(f"collect:{slug}")

    def auto_next_run_at(slug: str) -> Optional[str]:
        return job_next_run_at(f"publish:{slug}")

    def add_account_jobs(runtime: AccountRuntime) -> None:
        slug = runtime.account.slug
        trigger = CronTrigger.from_crontab(
            schedule_store.collect_cron(slug), timezone=app_settings.app_timezone
        )
        scheduler.add_job(
            scheduled_collect, trigger, args=[slug], id=f"collect:{slug}", replace_existing=True
        )
        if runtime.account.schedule.auto_publish:
            auto_trigger = CronTrigger.from_crontab(
                schedule_store.auto_publish_cron(slug), timezone=app_settings.app_timezone
            )
            scheduler.add_job(
                scheduled_publish, auto_trigger, args=[slug],
                id=f"publish:{slug}", replace_existing=True,
            )

    def reschedule_account(runtime: AccountRuntime) -> None:
        slug = runtime.account.slug
        if not scheduler.running:
            return
        collect_trigger = CronTrigger.from_crontab(
            schedule_store.collect_cron(slug), timezone=app_settings.app_timezone
        )
        if scheduler.get_job(f"collect:{slug}"):
            scheduler.reschedule_job(f"collect:{slug}", trigger=collect_trigger)
        else:
            scheduler.add_job(
                scheduled_collect, collect_trigger, args=[slug],
                id=f"collect:{slug}", replace_existing=True,
            )
        if runtime.account.schedule.auto_publish:
            auto_trigger = CronTrigger.from_crontab(
                schedule_store.auto_publish_cron(slug), timezone=app_settings.app_timezone
            )
            if scheduler.get_job(f"publish:{slug}"):
                scheduler.reschedule_job(f"publish:{slug}", trigger=auto_trigger)
            else:
                scheduler.add_job(
                    scheduled_publish, auto_trigger, args=[slug],
                    id=f"publish:{slug}", replace_existing=True,
                )
        elif scheduler.get_job(f"publish:{slug}"):
            scheduler.remove_job(f"publish:{slug}")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if enable_scheduler:
            for runtime in runtimes:
                try:
                    add_account_jobs(runtime)
                except ValueError:
                    log.warning("invalid cron for account %s; scheduler skipped", runtime.account.slug)
            scheduler.start()
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="XHS Content Workflow", version="0.3.0", lifespan=lifespan)

    if app_settings.app_password and app_settings.xhs_media_base_url and not app_settings.app_media_token:
        log.warning("APP_PASSWORD is set but APP_MEDIA_TOKEN is empty; MCP image URLs will be rejected")

    def resolve_account(account: Optional[str] = None) -> AccountRuntime:
        slug = (account or default_slug).strip()
        runtime = registry.get(slug)
        if not runtime:
            raise HTTPException(404, f"unknown or disabled account: {slug}")
        return runtime

    def runtime_for_post(post: dict[str, Any]) -> AccountRuntime:
        runtime = registry.get(str(post.get("account_slug") or ""))
        if not runtime:
            raise HTTPException(409, "post belongs to a disabled or missing account")
        return runtime

    def publisher_status(account: AccountConfig) -> dict[str, Any]:
        return {
            "mode": account.publish.mode,
            "configured": _publisher_ready(account),
            "visibility": account.publish.visibility,
        }

    def provider_status(account: AccountConfig) -> dict[str, Any]:
        status: dict[str, Any] = {}
        sources = account.sources
        if sources.reddit is not None:
            cfg = sources.reddit
            mode = str(cfg.get("mode") or "auto")
            has_api = bool(
                os.getenv(str(cfg.get("client_id_env") or "REDDIT_CLIENT_ID"), "")
                or app_settings.reddit_client_id
            ) and bool(
                os.getenv(str(cfg.get("client_secret_env") or "REDDIT_CLIENT_SECRET"), "")
                or app_settings.reddit_client_secret
            )
            status["reddit"] = has_api if mode == "api" else True
        if sources.x is not None:
            cfg = sources.x
            status["x"] = bool(
                os.getenv(str(cfg.get("bearer_token_env") or "X_BEARER_TOKEN"), "")
                or app_settings.x_bearer_token
            )
        if sources.youtube is not None:
            cfg = sources.youtube
            status["youtube"] = bool(
                os.getenv(str(cfg.get("api_key_env") or "YOUTUBE_API_KEY"), "")
                or app_settings.youtube_api_key
            )
        if sources.rss:
            status["web"] = True
        if sources.weibo is not None:
            cfg = sources.weibo
            status["weibo"] = bool(cfg.get("uids") or cfg.get("feeds"))
        if sources.instagram is not None:
            status["instagram"] = bool((sources.instagram or {}).get("feeds"))
        return status

    def account_summary(runtime: AccountRuntime) -> dict[str, Any]:
        account = runtime.account
        stats = db.stats(
            account_id=account.id,
            timezone_name=app_settings.app_timezone,
            ai_only=account.relevance_only,
            min_ai_score=account.min_score,
            target=account.target_posts_per_day,
        )
        return {
            "slug": account.slug,
            "name": account.name,
            "avatar": account.avatar,
            "accent": account.accent,
            "enabled": account.enabled,
            "content_style": account.content_style,
            "tagline": account.tagline,
            "description": account.description,
            "platforms": account.platforms,
            "target_posts_per_day": account.target_posts_per_day,
            "captured_today": stats["captured_today"],
            "drafts": stats["drafts"],
            "approved": stats["approved"],
            "published_today": stats["published_today"],
            "total": stats["total"],
            "publisher_mode": account.publish.mode,
            "publisher_configured": _publisher_ready(account),
            "visibility": account.publish.visibility,
            "collect_cron": schedule_store.collect_cron(account.slug),
            "auto_publish": account.schedule.auto_publish,
            "auto_publish_cron": schedule_store.auto_publish_cron(account.slug),
            "auto_publish_limit": schedule_store.auto_publish_limit(account.slug),
            "next_run_at": next_run_at(account.slug),
            "auto_publish_next_run_at": auto_next_run_at(account.slug) if account.schedule.auto_publish else None,
            "scheduler_running": scheduler.running,
        }

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if not app_settings.app_password:
            return await call_next(request)
        if request.url.path == "/health":
            return await call_next(request)
        if request.url.path.startswith("/media/") and app_settings.app_media_token:
            token = request.query_params.get("token", "")
            if secrets.compare_digest(token, app_settings.app_media_token):
                return await call_next(request)
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
                username, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                pass
            else:
                if (secrets.compare_digest(username, app_settings.app_username)
                        and secrets.compare_digest(password, app_settings.app_password)):
                    return await call_next(request)
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Signal Atelier", charset="UTF-8"'},
            content="Authentication required",
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        default = registry[default_slug]
        return {
            "ok": True,
            "publisher": default.account.publish.mode,
            "target_posts_per_day": default.account.target_posts_per_day,
            "scheduler_running": scheduler.running,
            "next_run_at": next_run_at(default_slug),
            "accounts": [
                {
                    "slug": runtime.account.slug,
                    "name": runtime.account.name,
                    "publisher_configured": _publisher_ready(runtime.account),
                }
                for runtime in runtimes
            ],
        }

    @app.get("/accounts")
    def accounts() -> list[dict[str, object]]:
        return [account_summary(runtime) for runtime in runtimes]

    @app.get("/status")
    def status(account: Optional[str] = None) -> dict[str, object]:
        runtime = resolve_account(account)
        current = runtime.account
        return {
            "account": {
                "slug": current.slug,
                "name": current.name,
                "avatar": current.avatar,
                "accent": current.accent,
                "content_style": current.content_style,
                "tagline": current.tagline,
                "description": current.description,
                "platforms": current.platforms,
            },
            "app": {
                "timezone": app_settings.app_timezone,
                "collect_cron": schedule_store.collect_cron(current.slug),
                "scheduler_running": scheduler.running,
                "next_run_at": next_run_at(current.slug),
            },
            "llm": {
                "configured": bool(app_settings.llm_api_key and app_settings.llm_model),
                "base_url": app_settings.llm_base_url,
                "model": app_settings.llm_model or None,
            },
            "providers": provider_status(current),
            "reddit_stream": _reddit_stream(current),
            "rss": {
                "enabled": bool(current.sources.rss),
                "feeds": len(current.sources.rss),
            },
            "social": {
                "weibo": {
                    "uids": len((current.sources.weibo or {}).get("uids") or []),
                    "feeds": len((current.sources.weibo or {}).get("feeds") or []),
                    "cookie_configured": bool(
                        os.getenv(str((current.sources.weibo or {}).get("cookie_env") or "WEIBO_COOKIE"), "")
                    ),
                },
                "instagram": {
                    "feeds": len((current.sources.instagram or {}).get("feeds") or []),
                },
            },
            "x_streams": {
                "list_configured": bool((current.sources.x or {}).get("list_id")),
                "priority_accounts": len((current.sources.x or {}).get("priority_accounts") or []),
                "priority_all": bool((current.sources.x or {}).get("priority_all")),
                "priority_boost": (current.sources.x or {}).get("priority_boost", 0),
            },
            "publisher": publisher_status(current),
            "automation": {
                "auto_publish": current.schedule.auto_publish,
                "auto_publish_cron": schedule_store.auto_publish_cron(current.slug),
                "auto_publish_limit": schedule_store.auto_publish_limit(current.slug),
                "auto_publish_today_only": True,
                "auto_publish_next_run_at": (
                    auto_next_run_at(current.slug) if current.schedule.auto_publish else None
                ),
            },
            "filters": {
                "ai_only": current.relevance_only,
                "min_ai_score": current.min_score,
                "ai_keywords": len(current.keywords),
            },
            "media": {
                "rights_mode": current.media.rights_mode,
                "allow_unverified_repost": current.media.allow_unverified,
                "max_images": current.media.max_images,
                "min_images": current.media.min_images,
                "max_bytes": app_settings.media_max_bytes,
            },
            "target_posts_per_day": current.target_posts_per_day,
        }

    @app.patch("/settings/schedule")
    def update_schedule(payload: ScheduleUpdate, account: Optional[str] = None) -> dict[str, object]:
        if payload.collect_time is None and payload.auto_publish_time is None and payload.auto_publish_limit is None:
            raise HTTPException(422, "nothing to update")
        runtime = resolve_account(account)
        slug = runtime.account.slug
        collect_cron = schedule_store.collect_cron(slug)
        auto_cron = schedule_store.auto_publish_cron(slug)
        try:
            if payload.collect_time is not None:
                collect_cron = validate_cron(time_to_cron(payload.collect_time))
            if payload.auto_publish_time is not None:
                auto_cron = validate_cron(time_to_cron(payload.auto_publish_time))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        schedule_store.update(
            slug,
            collect_cron=collect_cron,
            auto_publish_cron=auto_cron,
            auto_publish_limit=payload.auto_publish_limit,
        )
        schedule_store.save()
        runtime.publish_service.auto_publish_limit = schedule_store.auto_publish_limit(slug)
        reschedule_account(runtime)

        return {
            "ok": True,
            "account": slug,
            "collect_cron": collect_cron,
            "auto_publish_cron": auto_cron,
            "auto_publish_limit": schedule_store.auto_publish_limit(slug),
            "next_run_at": next_run_at(slug),
            "auto_publish_next_run_at": (
                auto_next_run_at(slug) if runtime.account.schedule.auto_publish else None
            ),
        }

    @app.get("/stats")
    def stats(account: Optional[str] = None) -> dict[str, object]:
        runtime = resolve_account(account)
        current = runtime.account
        data: dict[str, Any] = db.stats(
            account_id=current.id,
            timezone_name=app_settings.app_timezone,
            ai_only=current.relevance_only,
            min_ai_score=current.min_score,
            target=current.target_posts_per_day,
        )
        data["account"] = current.slug
        data["next_run_at"] = next_run_at(current.slug)
        data["scheduler_running"] = scheduler.running
        return data

    @app.post("/run")
    def run(account: Optional[str] = None) -> dict[str, object]:
        runtime = resolve_account(account)
        return runtime.pipeline.run_once(trigger="manual")

    @app.get("/posts")
    def posts(status: Optional[str] = None, limit: int = 100,
              account: Optional[str] = None) -> list[dict[str, object]]:
        runtime = resolve_account(account)
        return db.list_posts(
            status=status, limit=min(limit, 500), account_id=runtime.account.id
        )

    @app.get("/hot")
    def hot(limit: int = 30, account: Optional[str] = None) -> list[dict[str, object]]:
        """Return the active hot queue, ordered by heat score."""
        runtime = resolve_account(account)
        current = runtime.account
        return db.list_posts(
            limit=min(limit, 100),
            ai_only=current.relevance_only,
            min_ai_score=current.min_score,
            statuses=("draft", "approved", "published"),
            account_id=current.id,
        )

    @app.get("/posts/{post_id}")
    def get_post(post_id: int) -> dict[str, object]:
        post = db.get_post(post_id)
        if not post:
            raise HTTPException(404, "post not found")
        return post

    @app.patch("/posts/{post_id}")
    def update_post(post_id: int, payload: PostUpdate) -> dict[str, object]:
        post = db.get_post(post_id)
        if not post:
            raise HTTPException(404, "post not found")
        title = payload.title.strip() if payload.title is not None else None
        body = payload.body.strip() if payload.body is not None else None
        if payload.title is not None and not title:
            raise HTTPException(422, "title must not be empty")
        if payload.body is not None and not body:
            raise HTTPException(422, "body must not be empty")
        tags = None
        if payload.tags is not None:
            tags = []
            for raw in payload.tags:
                tag = raw.strip().lstrip("#")
                if tag and tag not in tags:
                    tags.append(tag)
        if title is None and body is None and tags is None:
            raise HTTPException(422, "nothing to update")
        db.update_post(post_id, title=title, body=body, tags=tags)
        return {"ok": True, "post": db.get_post(post_id)}

    @app.post("/posts/{post_id}/approve")
    def approve(post_id: int) -> dict[str, object]:
        post = db.get_post(post_id)
        if not post:
            raise HTTPException(404, "post not found")
        if post["status"] == "published":
            raise HTTPException(409, "published post cannot be approved again")
        db.update_status(post_id, "approved")
        return {"ok": True, "post_id": post_id, "status": "approved"}

    @app.post("/posts/{post_id}/reject")
    def reject(post_id: int) -> dict[str, object]:
        post = db.get_post(post_id)
        if not post:
            raise HTTPException(404, "post not found")
        if post["status"] == "published":
            raise HTTPException(409, "published post cannot be rejected")
        db.update_status(post_id, "rejected")
        return {"ok": True, "post_id": post_id, "status": "rejected"}

    @app.post("/posts/{post_id}/publish")
    def publish(post_id: int) -> dict[str, object]:
        post = db.get_post(post_id)
        if not post:
            raise HTTPException(404, "post not found")
        if post["status"] != "approved":
            raise HTTPException(409, "post must be approved before publishing")
        runtime = runtime_for_post(post)
        try:
            result = runtime.publish_service.publish_one(post)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            log.exception("publish failed for post %s", post_id)
            raise HTTPException(502, f"publisher failed: {exc}") from exc
        return {"ok": True, "post_id": post_id, "result": result}

    @app.post("/queue/publish")
    def publish_queue(limit: int = 10, account: Optional[str] = None) -> dict[str, object]:
        runtime = resolve_account(account)
        return runtime.publish_service.publish_queue(limit=max(1, min(limit, 50)))

    @app.get("/posts/{post_id}/markdown")
    def markdown(post_id: int) -> PlainTextResponse:
        post = db.get_post(post_id)
        if not post:
            raise HTTPException(404, "post not found")
        return PlainTextResponse(
            render_markdown(post),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="xhs-post-{post_id}.md"'},
        )

    app.mount("/media", StaticFiles(directory=app_settings.media_dir), name="media")

    # The dashboard is intentionally served by the same process so local deployments
    # do not need a second frontend server or a separate CORS configuration.
    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=web_dir), name="static")
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app


def _reddit_stream(account: AccountConfig) -> dict[str, object]:
    cfg = account.sources.reddit or {}
    mode = str(cfg.get("mode") or "auto")
    has_api = bool(
        os.getenv(str(cfg.get("client_id_env") or "REDDIT_CLIENT_ID"), "")
    ) and bool(
        os.getenv(str(cfg.get("client_secret_env") or "REDDIT_CLIENT_SECRET"), "")
    )
    if mode == "api":
        resolved = "api"
    elif mode == "rss":
        resolved = "rss"
    else:
        resolved = "api" if has_api else "rss"
    return {"mode": resolved, "ready": has_api if resolved == "api" else True}