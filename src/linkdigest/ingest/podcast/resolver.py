"""播客路线收口：短链展开 → 平台各自的元数据解析 → 下载 enclosure → 组装来源元信息。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from ...contracts.models import SourceMeta
from ...net import http
from ...net.errors import DownloadFailure
from ...net.transfer import stream_to
from ..classify import audio_suffix, canonicalize
from ..platforms import SHORT_LINK_HOSTS
from ..result import IngestResult
from . import apple, spotify
from .target import PodcastTarget


def expand_short(url: str) -> str:
    """spotify.link / apple.co 这类短链先跟一次重定向，拿到真实页面。"""
    host = (urlsplit(url).hostname or "").lower()
    if not any(host == d or host.endswith("." + d) for d in SHORT_LINK_HOSTS):
        return url
    try:
        with http.client(timeout=10.0) as session:
            return str(session.get(url).url) or url
    except Exception:  # noqa: BLE001 - 短链失败就按原链接继续，后面的解析会给出具体原因
        return url


def _target(url: str, platform: str) -> PodcastTarget:
    return spotify.resolve(url) if platform == "spotify" else apple.resolve(url)


def ingest(url: str, workdir: Path, platform: str) -> IngestResult:
    url = expand_short(url)
    target = _target(url, platform)
    if not target.audio_url:
        raise DownloadFailure("解析到了元数据但没有音频直链，换个链接或上传音频文件。")

    media = stream_to(target.audio_url, workdir / f"source{audio_suffix(target.audio_url)}")
    meta = SourceMeta(
        kind="audio",
        platform=platform,
        url=url,
        canonical_url=canonicalize(url),
        title=target.title,
        author=target.show,
        published_at=target.published_at,
        duration_sec=target.duration_sec,
        extractor=target.route,
        extra={"thumbnail": target.thumbnail, "audio_url": target.audio_url, "feed_url": target.feed_url},
    )
    notes = [f"音源跳过了播放器，从原始 RSS 取 enclosure：{target.route}"]
    if "最新一集" in target.route:
        notes.append("链接指向节目主页而不是单集，已取最新一集；要精确解析请在链接里带上 ?i=单集ID。")
    return IngestResult(source=meta, media_file=media, notes=notes)
