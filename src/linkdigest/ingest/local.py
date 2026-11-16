"""本地文件：文本直接进管道，音视频交给上层抽轨。"""

from __future__ import annotations

from pathlib import Path

from ..contracts.models import SourceMeta
from .article import extract_text
from .platforms import TEXT_EXT
from .result import IngestResult


def ingest(path: Path) -> IngestResult:
    if path.suffix.lower() in TEXT_EXT:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        text = extract_text(raw) if path.suffix.lower() in {".html", ".htm"} else raw
        meta = SourceMeta(
            kind="article",
            platform="local",
            url=None,
            canonical_url=None,
            title=path.stem,
            extractor="filesystem-text",
            extra={"filename": path.name},
        )
        return IngestResult(source=meta, text=text, notes=["本地文本直接进管道"])

    meta = SourceMeta(
        kind="local",
        platform="local",
        url=None,
        canonical_url=None,
        title=path.stem,
        extractor="filesystem",
        extra={"filename": path.name},
    )
    return IngestResult(source=meta, media_file=path)
