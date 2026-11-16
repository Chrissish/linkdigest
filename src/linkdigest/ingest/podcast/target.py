"""解析目标：三级路线走到哪一步、拿到了什么。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PodcastTarget:
    title: str | None = None
    show: str | None = None
    published_at: str | None = None
    duration_sec: float | None = None
    thumbnail: str | None = None
    audio_url: str | None = None
    feed_url: str | None = None
    route: str = ""
