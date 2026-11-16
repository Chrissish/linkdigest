"""iTunes 公开接口（免密钥）。播客世界的「DNS」：按标题找集、按节目找 feed。

只做请求与字段整形，不做匹配判断——阈值在调用方，因为不同路线容错不同。
"""

from __future__ import annotations

from urllib.parse import quote

from ...net.transfer import get_json
from ..platforms import ITUNES_LOOKUP, ITUNES_SEARCH
from .target import PodcastTarget


def episodes(term: str, limit: int = 12) -> list[dict]:
    data = get_json(f"{ITUNES_SEARCH}?term={quote(term)}&entity=podcastEpisode&media=podcast&limit={limit}")
    return list(data.get("results") or [])


def feeds(term: str, limit: int = 6) -> list[str]:
    data = get_json(f"{ITUNES_SEARCH}?term={quote(term)}&entity=podcast&media=podcast&limit={limit}")
    out: list[str] = []
    for row in data.get("results") or []:
        feed = row.get("feedUrl")
        if feed and feed not in out:
            out.append(feed)
    return out


def lookup(target_id: str, limit: int | None = None) -> list[dict]:
    query = f"{ITUNES_LOOKUP}?id={target_id}&entity=podcastEpisode&media=podcast"
    if limit:
        query += f"&limit={limit}"
    return list(get_json(query).get("results") or [])


def to_target(row: dict, route: str) -> PodcastTarget | None:
    """一行 iTunes 结果 → 目标。没有音频直链就没有意义，返回 None。"""
    audio = row.get("episodeUrl") or row.get("previewUrl")
    if not audio:
        return None
    millis = row.get("trackTimeMillis")
    return PodcastTarget(
        title=row.get("trackName"),
        show=row.get("collectionName"),
        published_at=row.get("releaseDate"),
        duration_sec=millis / 1000 if isinstance(millis, (int, float)) else None,
        thumbnail=row.get("artworkUrl600") or row.get("artworkUrl100"),
        audio_url=audio,
        feed_url=row.get("feedUrl"),
        route=route,
    )
