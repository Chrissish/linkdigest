"""播客 RSS：feedparser 读条目，取第一条的音频 enclosure。"""

from __future__ import annotations

from ..contracts.models import SourceMeta
from .result import IngestResult


def entry_audio(entry) -> str | None:
    """从 RSS/GUID 条目里找音频直链，enclosure 优先，其次带 audio type 的 link。"""
    for enclosure in entry.get("enclosures") or []:
        href = enclosure.get("href")
        if href and (
            (enclosure.get("type") or "").startswith("audio")
            or href.split("?")[0].endswith((".mp3", ".m4a", ".ogg", ".opus"))
        ):
            return href
    for link in entry.get("links") or []:
        if link.get("href") and "audio" in (link.get("type") or ""):
            return link["href"]
    return None


def ingest(url: str) -> IngestResult:
    import feedparser

    feed = feedparser.parse(url)
    if not feed.entries:
        raise RuntimeError("RSS 中没有条目")
    entry = feed.entries[0]
    audio_url = entry_audio(entry)
    if not audio_url:
        raise RuntimeError("该 RSS 条目没有音频 enclosure")

    meta = SourceMeta(
        kind="audio",
        platform="rss",
        url=url,
        canonical_url=entry.get("link") or url,
        title=entry.get("title"),
        author=feed.feed.get("title"),
        published_at=entry.get("published"),
        extractor=f"feedparser/{feed.version or 'rss'}",
        extra={"audio_url": audio_url},
    )
    return IngestResult(source=meta, notes=["RSS 直取音频，跳过解析"])


def matches(feed_url: str, title: str | None, threshold: float = 0.6):
    """在 feed 里按标题找那一集。取不到就返回 None，不猜最新一集。"""
    import feedparser

    from .podcast.titles import title_score

    feed = feedparser.parse(feed_url)
    best, best_score = None, 0.0
    for entry in feed.entries or []:
        score = title_score(title, entry.get("title"))
        if score > best_score:
            best, best_score = entry, score
    if best is None or best_score < threshold or not entry_audio(best):
        return None
    return best
