"""播客网页（小宇宙、喜马拉雅等）：直接从 HTML 里捞音频直链。"""

from __future__ import annotations

import re

from ..config import Settings
from ..contracts.models import SourceMeta
from ..net.http import get_text
from .classify import detect_platform
from .platforms import AUDIO_URL_RE
from .result import IngestResult


def ingest(cfg: Settings, url: str) -> IngestResult | None:
    try:
        html = get_text(cfg, url)
    except Exception:  # noqa: BLE001 - 捞不到就走别的路，不在这里报错
        return None
    match = AUDIO_URL_RE.search(html)
    if not match:
        return None

    audio_url = match.group(0)
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    meta = SourceMeta(
        kind="audio",
        platform=detect_platform(url),
        url=url,
        canonical_url=url,
        title=re.sub(r"\s+", " ", title.group(1)).strip() if title else None,
        extractor="html-audio-scrape",
        extra={"audio_url": audio_url},
    )
    return IngestResult(source=meta, notes=["从页面 HTML 直接提取音频直链"])
