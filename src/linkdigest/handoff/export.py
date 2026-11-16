"""把交接包写到磁盘：BRIEF、纯文本转写稿、切片、答案模板。"""

from __future__ import annotations

import json
from pathlib import Path

from ..contracts.models import TranscriptBundle
from ..conventions.timecode import ts
from ..jobs.layout import job_dir
from .brief import build_brief, required_schema, split_parts, transcript_lines
from .paths import ANSWER, BRIEF, TEMPLATE, TRANSCRIPT_MD


def export_handoff(job_id: str, bundle: TranscriptBundle) -> Path:
    """写 handoff/ 目录，返回 answer.json 的期望路径。"""
    directory = job_dir(job_id)
    handoff_dir = directory / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)

    lines = transcript_lines(bundle)
    parts = split_parts(lines)
    if len(parts) > 1:
        for i, part in enumerate(parts, 1):
            (handoff_dir / f"part-{i:02d}.md").write_text(
                f"# {bundle.source.title or '未命名内容'} · 第 {i}/{len(parts)} 片\n\n{part}\n",
                encoding="utf-8",
            )

    transcript_md = [
        f"# {bundle.source.title or '未命名内容'}",
        "",
        f"_{bundle.source.platform} · {bundle.quality.segment_count} 段 / "
        f"{bundle.quality.chars} 字 · 原语言 {bundle.language.detected}_",
        "",
    ]
    for chapter in bundle.chapters:
        transcript_md.append(f"## {chapter.title}（{ts(chapter.start)}–{ts(chapter.end)}）")
        transcript_md.append("")
    transcript_md.extend(["## 全文", *lines])
    (directory / TRANSCRIPT_MD).write_text("\n".join(transcript_md) + "\n", encoding="utf-8")

    (directory / BRIEF).write_text(build_brief(bundle, job_id, directory), encoding="utf-8")
    (directory / TEMPLATE).write_text(
        json.dumps(required_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return directory / ANSWER
