"""Spotify 单集：oEmbed 拿元数据 → iTunes 反查原始音源。

音乐内容（track/album/playlist/artist）的判定放在这里，不放在错误解释里：
那是路线选择问题（该走哪条路），不是网络故障。
"""

from __future__ import annotations

from urllib.parse import quote

from ...config import Settings
from ...net.errors import DownloadFailure
from ...net.transfer import get_json
from ..feed import entry_audio, matches
from ..platforms import SPOTIFY_ID, SPOTIFY_OEMBED
from . import itunes
from .target import PodcastTarget
from .titles import title_score

# 单集标题直接搜到的命中阈值。0.72 是一次次错配调出来的：
# 再松会把同一节目的相邻集当成目标。
DIRECT_HIT = 0.72


def resolve(cfg: Settings, url: str) -> PodcastTarget:
    match = SPOTIFY_ID.search(url)
    if not match:
        raise DownloadFailure(
            "没能从这条 Spotify 链接里认出内容 ID。支持 /episode/、/show/、/track/ 三种页面，"
            "确认链接是 open.spotify.com 下的完整地址。"
        )
    kind = match.group("kind").lower()
    if kind == "show":
        raise DownloadFailure(
            "这是 Spotify 的节目主页，不是一个单集，无法确定要解析哪一集。"
            "打开具体某一集，复制带 /episode/ 的链接（手机端点「分享 → 复制链接」即可）。"
        )
    if kind in {"track", "album", "playlist", "artist"}:
        raise DownloadFailure(
            f"Spotify 的 {kind} 属于音乐内容，音轨是 Widevine 加密流，任何工具都拿不到——"
            "这不是解析器能力问题。本程序处理的是播客和可下载音视频，音乐请手动上传音频文件。"
        )

    meta = get_json(cfg, f"{SPOTIFY_OEMBED}?url={quote(url, safe='')}")
    target = PodcastTarget(
        title=(meta.get("title") or "").strip() or None,
        thumbnail=meta.get("thumbnail_url"),
        route="spotify-oembed",
    )
    if not target.title:
        raise DownloadFailure("Spotify oEmbed 没返回标题，这条单集可能已下架或被设为私密。")

    # 第一跳：按单集标题直接在播客库里搜（iTunes Search 覆盖了绝大多数公开播客）
    best, score = None, 0.0
    for row in itunes.episodes(cfg, target.title):
        current = title_score(target.title, row.get("trackName"))
        if current > score:
            best, score = row, current
    if best and score >= DIRECT_HIT:
        found = itunes.to_target(best, "spotify-oembed → itunes-search → enclosure")
        if found:
            found.title = found.title or target.title
            found.thumbnail = found.thumbnail or target.thumbnail
            return found

    # 第二跳：标题被平台改写过时，退到「搜节目 → 解析 feed → 在 feed 里模糊匹配」
    for feed_url in itunes.feeds(cfg, target.title)[:4]:
        entry = matches(feed_url, target.title)
        if not entry:
            continue
        target.audio_url = entry_audio(entry)
        target.title = entry.get("title") or target.title
        target.feed_url = feed_url
        target.published_at = entry.get("published")
        target.route = "spotify-oembed → itunes-podcast → rss-enclosure"
        return target

    raise DownloadFailure(
        f"已取到元数据《{target.title}》，但在公开播客库里没找到对应音源。"
        "最可能是 Spotify 独家节目（Exclusive）——这类节目没有公开 RSS，音频只存在于 Spotify 的加密流里。"
        "可行的下一步：在 Spotify 客户端下载该集后导出音频上传，或用其它平台（Apple Podcasts / 小宇宙 / 官网）"
        "上同一个节目的公开链接。"
    )
