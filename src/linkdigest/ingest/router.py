"""采集路由：决定一个链接该走哪条路，并设定固定的降级顺序。

设计原则：视频和音频走同一条路 —— 只取音轨，永不抓帧、永不做画面理解。
失败降级顺序固定，不做无边界重试：

    本地文件 → 播客平台（独立路线） → 正文抽取 → RSS / 页面直链 → yt-dlp 兜底

正文抽取失败不算致命：先记住原因，如果后面按音视频也解析不出来，
两头的原因一起报给用户，避免「只说了其中一半」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from ..config import Settings
from ..net.errors import DownloadFailure, explain_error
from ..net.transfer import stream_to
from . import article, feed, html_audio, local, media_download
from .classify import audio_suffix, classify, detect_platform
from .normalize import normalize_input
from .platforms import HTML_AUDIO_PLATFORMS, PODCAST_PLATFORMS
from .podcast import resolver as podcast
from .result import IngestResult


def fetch(
    cfg: Settings, url: str, workdir: Path, progress: Callable[[float], None] | None = None
) -> IngestResult:
    url = normalize_input(url)
    workdir.mkdir(parents=True, exist_ok=True)

    path = Path(url).expanduser()
    if path.exists() and path.is_file():
        return local.ingest(path)

    platform = detect_platform(url)
    article_error: str | None = None

    # 播客平台单独一条路：页面给不出音轨，必须去原始 RSS 取。
    if platform in PODCAST_PLATFORMS:
        return podcast.ingest(cfg, url, workdir, platform)

    if platform == "wechat" or (platform == "generic" and not Path(urlsplit(url).path).suffix):
        try:
            return article.ingest(cfg, url)
        except Exception as exc:  # noqa: BLE001 - 留作后面的合并报错
            article_error = explain_error(exc, url, cfg)

    if platform == "rss":
        result = feed.ingest(cfg, url)
        audio_url = result.source.extra.get("audio_url")
        result.media_file = stream_to(cfg, audio_url, workdir / f"source{audio_suffix(audio_url)}")
        return result

    scraped = html_audio.ingest(cfg, url) if platform in HTML_AUDIO_PLATFORMS else None
    if scraped and scraped.source.extra.get("audio_url"):
        audio_url = scraped.source.extra["audio_url"]
        scraped.media_file = stream_to(cfg, audio_url, workdir / f"source{audio_suffix(audio_url)}")
        if article_error:
            scraped.notes.append(article_error)
        return scraped

    try:
        media, info = media_download.download(cfg, url, workdir, progress)
    except DownloadFailure as exc:
        if article_error:
            raise DownloadFailure(f"{article_error}；按音视频解析也不成立：{exc}") from exc
        raise

    meta = media_download.source_meta(
        info, url, platform, "video" if platform != "generic" else classify(url)
    )
    return IngestResult(source=meta, media_file=media, notes=[article_error] if article_error else [])
