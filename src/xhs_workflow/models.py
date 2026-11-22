from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SourceItem:
    platform: str
    source_id: str
    source_url: str
    author: str
    title: str
    text: str
    published_at: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    media_urls: list[str] = field(default_factory=list)
    rights_status: str = "unknown"
    heat_score: float = 0.0
    heat_label: str = ""
    ai_score: float = 0.0
    priority: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def image_urls(self) -> list[str]:
        output: list[str] = []
        for url in [*(self.media_urls or []), self.media_url or ""]:
            if url and url not in output:
                output.append(url)
        return output


@dataclass
class Draft:
    title: str
    body: str
    tags: list[str]
    source_note: str
    risk_flags: list[str]
