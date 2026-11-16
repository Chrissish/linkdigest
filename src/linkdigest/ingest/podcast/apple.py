"""Apple Podcasts：iTunes Lookup 一步到位，页面里本来就没有 mp3。"""

from __future__ import annotations

from ...net.errors import DownloadFailure
from ..platforms import APPLE_PODCAST_ID
from . import itunes
from .target import PodcastTarget


def resolve(url: str) -> PodcastTarget:
    match = APPLE_PODCAST_ID.search(url)
    if not match:
        raise DownloadFailure("Apple Podcasts 链接里没有找到 id，确认形式为 /podcast/名称/id123456789?i=1000600000000。")
    episode_id, show_id = match.group("episode"), match.group("show")

    if episode_id:
        for row in itunes.lookup(episode_id):
            target = itunes.to_target(row, "apple-lookup → enclosure")
            if target:
                return target

    # 只给了节目 id：iTunes lookup 会返回该节目最近一集，取它并在 notes 里说明
    for row in itunes.lookup(show_id, limit=1):
        target = itunes.to_target(row, "apple-lookup → 节目最新一集")
        if target:
            return target
    raise DownloadFailure(f"Apple Podcasts 查不到 id={episode_id or show_id} 的音频，节目可能已下架或仅限订阅。")
