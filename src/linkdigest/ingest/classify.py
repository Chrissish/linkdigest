"""链接判定：是什么平台、算哪一类、规范形式是什么。

全是纯函数，不出网。判定只影响走哪条采集路线，不影响最终契约。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

from .platforms import (
    ARTICLE_PLATFORMS,
    AUDIO_EXT,
    AUDIO_PLATFORMS,
    MEDIA_EXT,
    PLATFORMS,
    SPOTIFY_ID,
    VIDEO_PLATFORMS,
)


def detect_platform(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for name, domains in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    if not host:
        return "local"
    if "feed" in url or url.endswith((".xml", ".rss")):
        return "rss"
    return "generic"


def classify(url: str) -> str:
    """kind: video | audio | article | local"""
    path = Path(url).expanduser()
    if path.exists() and path.is_file():
        return "local"
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in AUDIO_EXT:
        return "video" if suffix in MEDIA_EXT else "audio"
    platform = detect_platform(url)
    if platform in VIDEO_PLATFORMS:
        return "video"
    if platform in ARTICLE_PLATFORMS:
        return "article"
    if platform in AUDIO_PLATFORMS:
        return "audio"
    # 未知域名默认按文章处理：正文抽取比 yt-dlp 泛解析更可能成功
    return "article"


def canonicalize(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.hostname in {"youtu.be"}:
        return f"https://www.youtube.com/watch?v={parts.path.strip('/')}"
    if parts.hostname and parts.hostname.endswith("youtube.com") and parts.path == "/watch":
        match = re.search(r"[?&]v=([\w-]{6,})", url)
        if match:
            return f"https://www.youtube.com/watch?v={match.group(1)}"
    if parts.hostname and parts.hostname.endswith("bilibili.com"):
        match = re.search(r"(BV[0-9A-Za-z]{8,}|av\d+)", url)
        if match:
            return f"https://www.bilibili.com/video/{match.group(1)}"
    # Spotify 的 ?si= 是分享追踪串、/intl-xx/ 是语言前缀，同一集会有多种写法，
    # 不清掉就会算出不同的 bundle_id，同一内容重复入库。
    if host.endswith("spotify.com"):
        match = SPOTIFY_ID.search(url)
        if match:
            return f"https://open.spotify.com/{match.group('kind')}/{match.group('id')}"
    return url.split("#")[0]


def bundle_id_for(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def audio_suffix(url: str) -> str:
    """给下载的临时文件挑个后缀。视频容器不算音频后缀，退回 mp3。"""
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in AUDIO_EXT - MEDIA_EXT else ".mp3"
