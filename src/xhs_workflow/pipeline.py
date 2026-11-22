from __future__ import annotations

import logging
import threading
from typing import Optional

from .accounts import AccountConfig, legacy_account
from .config import Settings
from .db import Database, start_of_local_day_iso
from .llm import LLM
from .media import download_media_list
from .models import SourceItem, now_iso
from .providers import Provider, build_providers

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings, db: Database, llm: LLM | None = None,
                 providers: list[Provider] | None = None,
                 account: Optional[AccountConfig] = None):
        self.settings = settings
        self.db = db
        self.account = account or legacy_account(settings)
        if self.account.id == 0:
            ids = db.sync_accounts([self.account])
            self.account.id = ids.get(self.account.slug, self.account.id)
        self.llm = llm or LLM(settings)
        self.providers = providers if providers is not None else build_providers(self.account, settings)
        self._run_lock = threading.Lock()

    def run_once(self, trigger: str = "manual") -> dict[str, object]:
        if not self._run_lock.acquire(blocking=False):
            return {"skipped": True, "reason": "a collection run is already in progress", "created": 0}

        started_at = now_iso()
        fetched = 0
        created = 0
        skipped = 0
        quota_reached = False
        remaining = 0
        errors: list[str] = []
        try:
            remaining = max(0, self.account.target_posts_per_day - self.db.count_posts_since(
                start_of_local_day_iso(self.settings.app_timezone), self.account.id
            ))
            if remaining == 0:
                quota_reached = True
                return {
                    "account": self.account.slug,
                    "trigger": trigger, "started_at": started_at, "finished_at": now_iso(),
                    "fetched": 0, "created": 0, "skipped": 0, "quota_reached": True,
                    "remaining_today": 0, "errors": [],
                }

            candidates = self._fetch_candidates(errors)
            fetched = len(candidates)
            candidates = self._dedupe_batch(candidates)
            candidates.sort(key=lambda item: (item.heat_score, item.ai_score), reverse=True)

            for item in candidates:
                if created >= remaining:
                    quota_reached = True
                    break
                if not item.source_url or not (item.title or item.text).strip():
                    skipped += 1
                    continue
                if (self.account.relevance_only and not item.priority
                        and item.ai_score < self.account.min_score):
                    skipped += 1
                    continue

                if self.db.source_exists(item, self.account.id):
                    skipped += 1
                    continue

                try:
                    draft = self.llm.transform(item, self.account)
                except Exception as exc:
                    log.warning("llm transform failed for %s: %s", item.source_url, exc)
                    errors.append(f"llm: {item.source_url}: {exc}")
                    skipped += 1
                    continue
                if item.platform == "youtube":
                    note = "仅基于视频标题/官方简介改写，未获取完整字幕"
                    if note not in draft.risk_flags:
                        draft.risk_flags.append(note)

                media_paths = download_media_list(item, self.account, self.settings)
                if self.account.is_gallery and not media_paths:
                    skipped += 1
                    errors.append(f"media: {item.source_url}: no usable images")
                    continue
                if media_paths and item.rights_status not in ("authorized", "cc"):
                    flag = "配图来自来源页面，转载前请确认版权"
                    if flag not in draft.risk_flags:
                        draft.risk_flags.append(flag)
                if (self.account.media.min_images
                        and media_paths
                        and len(media_paths) < self.account.media.min_images):
                    flag = f"图片不足 {self.account.media.min_images} 张"
                    if flag not in draft.risk_flags:
                        draft.risk_flags.append(flag)

                source_id = self.db.insert_source(item, self.account.id)
                if source_id is None:
                    skipped += 1
                    continue

                post_id = self.db.insert_post(
                    source_id, draft, media_paths=media_paths, account_id=self.account.id
                )
                if post_id is None:
                    skipped += 1
                    errors.append(f"post: {item.source_url}: skipped as duplicate")
                else:
                    created += 1
        except Exception as exc:
            log.exception("pipeline run failed")
            errors.append(f"pipeline: {exc}")
        finally:
            finished_at = now_iso()
            try:
                self.db.record_run(
                    trigger=trigger, started_at=started_at, finished_at=finished_at,
                    fetched=fetched, created=created, skipped=skipped,
                    quota_reached=quota_reached, errors=errors,
                    account_id=self.account.id,
                )
            except Exception:
                log.exception("failed to record collection run")
            self._run_lock.release()

        return {
            "account": self.account.slug,
            "trigger": trigger,
            "started_at": started_at,
            "finished_at": finished_at,
            "fetched": fetched,
            "created": created,
            "skipped": skipped,
            "quota_reached": quota_reached,
            "remaining_today": max(0, remaining - created),
            "errors": errors,
        }

    def _fetch_candidates(self, errors: list[str]) -> list[SourceItem]:
        candidates: list[SourceItem] = []
        per_provider = max(1, self.settings.fetch_per_provider)
        for provider in self.providers:
            try:
                candidates.extend(provider.fetch(per_provider))
            except Exception as exc:
                message = f"{provider.name}: {exc}"
                log.exception(message)
                errors.append(message)
        return candidates

    @staticmethod
    def _dedupe_batch(items: list[SourceItem]) -> list[SourceItem]:
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        output: list[SourceItem] = []
        for item in items:
            digest = Database.content_hash(item)
            if item.source_url in seen_urls or digest in seen_hashes:
                continue
            seen_urls.add(item.source_url)
            seen_hashes.add(digest)
            output.append(item)
        return output