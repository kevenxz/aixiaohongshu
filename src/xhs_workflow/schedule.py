from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from apscheduler.triggers.cron import CronTrigger

from .accounts import AccountConfig
from .config import Settings

log = logging.getLogger(__name__)


def validate_cron(expression: str) -> str:
    expression = expression.strip()
    try:
        CronTrigger.from_crontab(expression)
    except ValueError as exc:
        raise ValueError(f"invalid cron expression: {expression}") from exc
    return expression


def cron_to_time(expression: str) -> str:
    parts = expression.strip().split()
    if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
        return ""
    minute, hour = parts[0], parts[1]
    if not (minute.isdigit() and hour.isdigit()):
        return ""
    minute_i, hour_i = int(minute), int(hour)
    if not (0 <= minute_i <= 59 and 0 <= hour_i <= 23):
        return ""
    return f"{hour_i:02d}:{minute_i:02d}"


def time_to_cron(value: str) -> str:
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError as exc:
        raise ValueError("time must be HH:MM") from exc
    return f"{parsed.minute} {parsed.hour} * * *"


class ScheduleStore:
    def __init__(self, path: Path, settings: Settings, accounts: list[AccountConfig]):
        self.path = path
        self.accounts = accounts
        self.defaults: dict[str, dict[str, Any]] = {
            account.slug: {
                "collect_cron": account.schedule.collect_cron,
                "auto_publish_cron": account.schedule.auto_publish_cron,
                "auto_publish_limit": account.schedule.auto_publish_limit,
            }
            for account in accounts
        }
        self.values: dict[str, dict[str, Any]] = {
            slug: dict(values) for slug, values in self.defaults.items()
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("ignoring invalid schedule file %s", self.path)
            return
        if not isinstance(data, dict):
            return
        if isinstance(data.get("accounts"), dict):
            for slug, values in data["accounts"].items():
                if slug in self.values and isinstance(values, dict):
                    self._apply(slug, values)
        else:
            # v1 flat layout: assign legacy overrides to the first account.
            slug = next(iter(self.defaults), "")
            if slug:
                self._apply(slug, data)

    def _apply(self, slug: str, values: dict[str, Any]) -> None:
        target = self.values[slug]
        try:
            if values.get("collect_cron"):
                target["collect_cron"] = validate_cron(str(values["collect_cron"]))
            if values.get("auto_publish_cron"):
                target["auto_publish_cron"] = validate_cron(str(values["auto_publish_cron"]))
            limit = int(values.get("auto_publish_limit", target["auto_publish_limit"]))
            if 1 <= limit <= 10:
                target["auto_publish_limit"] = limit
        except (TypeError, ValueError):
            log.warning("ignoring invalid schedule values for %s", slug)

    def get(self, slug: str) -> dict[str, Any]:
        return self.values.get(slug) or self.values.get(next(iter(self.defaults), ""), {})

    def collect_cron(self, slug: str) -> str:
        return str(self.get(slug).get("collect_cron") or "0 8 * * *")

    def auto_publish_cron(self, slug: str) -> str:
        return str(self.get(slug).get("auto_publish_cron") or "30 9 * * *")

    def auto_publish_limit(self, slug: str) -> int:
        return int(self.get(slug).get("auto_publish_limit") or 3)

    def update(self, slug: str, collect_cron: Optional[str] = None,
               auto_publish_cron: Optional[str] = None,
               auto_publish_limit: Optional[int] = None) -> None:
        target = self.values.setdefault(slug, dict(self.defaults.get(slug, {})))
        if collect_cron is not None:
            target["collect_cron"] = validate_cron(collect_cron)
        if auto_publish_cron is not None:
            target["auto_publish_cron"] = validate_cron(auto_publish_cron)
        if auto_publish_limit is not None:
            target["auto_publish_limit"] = max(1, min(10, int(auto_publish_limit)))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"accounts": self.values}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)