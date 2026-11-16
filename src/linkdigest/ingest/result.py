"""采集结果：一个音频文件，或一段正文。两条路只有这一种出口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..contracts.models import SourceMeta
from ..net.errors import DownloadFailure


@dataclass(slots=True)
class IngestResult:
    source: SourceMeta
    media_file: Path | None = None
    text: str | None = None
    images: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


__all__ = ["DownloadFailure", "IngestResult"]
