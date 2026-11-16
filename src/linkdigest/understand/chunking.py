"""分块：按字数预算装箱，每块带时间范围与锚点前缀。

锚点有两种：有时间轴的用 `[mm:ss]`，文章类用 `[段N]`——模型据此知道
「引用位置」该写成什么，报告里的锚点才不会凭空出现。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..contracts.models import TranscriptBundle
from ..conventions.timecode import ts


@dataclass(slots=True)
class Chunk:
    start: float
    end: float
    body: str
    chars: int


def chunk_bundle(bundle: TranscriptBundle, max_chars: int | None = None) -> list[Chunk]:
    budget = max_chars or settings.llm_chunk_chars
    timeline = bundle.has_timeline
    chunks: list[Chunk] = []
    lines: list[str] = []
    size = 0
    start = bundle.segments[0].start if bundle.segments else 0.0
    end = start

    for seg in bundle.segments:
        anchor = f"[{ts(seg.start)}]" if timeline else f"[段{int(seg.start) + 1}]"
        line = f"{anchor} {seg.text}"
        if size + len(line) > budget and lines:
            chunks.append(Chunk(start=start, end=end, body="\n".join(lines), chars=size))
            lines, size = [], 0
            start = seg.start
        lines.append(line)
        size += len(line)
        end = seg.end

    if lines:
        chunks.append(Chunk(start=start, end=end, body="\n".join(lines), chars=size))
    return chunks


def digest_block(index: int, chunk: Chunk, digest: dict) -> str:
    points = "；".join(str(p) for p in (digest.get("points") or [])[:8])
    return (
        f"### 段 {index} [{ts(chunk.start)}-{ts(chunk.end)}]\n"
        f"{digest.get('summary', '')}\n"
        f"要点：{points}\n"
    )


def chapters_block(bundle: TranscriptBundle) -> str:
    return (
        "\n".join(
            f"- {c.title}: {ts(c.start)} - {ts(c.end)} (start={c.start:.0f}, end={c.end:.0f})"
            for c in bundle.chapters
        )
        or "（无）"
    )
