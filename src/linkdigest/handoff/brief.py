"""作业单渲染。

版式与全部说明文字在 `resources/templates/brief.md`，这里只做三件事：
把转写稿按预算切片、填元信息表、按「有时间轴 / 无时间轴」换掉两处口径。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from ..contracts.models import TranscriptBundle
from ..conventions.resources import text as resource_text
from ..conventions.timecode import ts
from .paths import ANSWER, INLINE_LIMIT


def meta_table(bundle: TranscriptBundle, job_id: str, directory: Path) -> str:
    src = bundle.source
    duration = bundle.duration
    rows = [
        ("标题", src.title or "（没抓到标题）"),
        ("平台", src.platform),
        ("作者", src.author or "未知"),
        ("发布", src.published_at or "未知"),
        ("源链接", src.canonical_url or src.url or "（本地文件）"),
        ("时长 / 篇幅", ts(duration) if duration else f"{bundle.quality.chars} 字"),
        ("原文语言", f"{bundle.language.detected}（置信度 {bundle.language.probability:.2f}）"),
        ("转写引擎", bundle.quality.asr_engine),
        (
            "质量",
            f"{bundle.quality.segment_count} 段 / {bundle.quality.chars} 字"
            f" / 平均置信度 {bundle.quality.avg_confidence if bundle.quality.avg_confidence is not None else 'n/a'}",
        ),
        ("任务目录", f"`{directory}`"),
        ("任务 id", f"`{job_id}`"),
    ]
    lines = ["| 字段 | 值 |", "| --- | --- |"]
    lines += [f"| {k} | {str(v).replace('|', '／')} |" for k, v in rows]
    return "\n".join(lines)


def chapters_block(bundle: TranscriptBundle) -> str:
    if not bundle.chapters:
        return "（无预先切分，按内容自行划分 3-8 个小节）"
    return "\n".join(f"- {c.title}：{ts(c.start)}–{ts(c.end)}" for c in bundle.chapters)


def transcript_lines(bundle: TranscriptBundle) -> list[str]:
    timeline = bundle.has_timeline
    lines: list[str] = []
    for seg in bundle.segments:
        anchor = f"[{ts(seg.start)}]" if timeline else f"[段{seg.idx}]"
        lines.append(f"{anchor} {seg.text}")
    return lines


def split_parts(lines: list[str], budget: int = INLINE_LIMIT) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) > budget and buf:
            parts.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts


def required_schema() -> dict:
    """必填五件套。模板只放这些——塞满字段会鼓励凑数。"""
    return json.loads(resource_text("templates/answer.template.json"))


def _transcript_body(parts: list[str]) -> str:
    if len(parts) > 1:
        return "正文较长，已切片，见 `handoff/part-*.md`。\n"
    block = "\n".join([f"```text\n{parts[0]}\n```", ""]) if parts else ""
    return block


def _parts_note(parts: list[str]) -> str:
    if len(parts) <= 1:
        return ""
    listing = "\n".join(f"- `handoff/part-{i:02d}.md`" for i in range(1, len(parts) + 1))
    return f"正文共 {len(parts)} 片，按顺序读完再动笔：\n\n{listing}\n"


def build_brief(bundle: TranscriptBundle, job_id: str, directory: Path) -> str:
    """自包含作业单：读它一个文件就能产出 answer.json。"""
    timeline = bundle.has_timeline
    parts = split_parts(transcript_lines(bundle))

    values = {
        "title": bundle.source.title or "未命名内容",
        "answer_path": str(directory / ANSWER),
        "answer_name": ANSWER,
        # 用当前解释器，不假设仓库里的 .venv；配置与数据目录的解析也不依赖 cwd
        "run_command": f"{sys.executable} -m linkdigest.cli {job_id} --apply",
        "meta_table": meta_table(bundle, job_id, directory),
        "parts_note": _parts_note(parts),
        "required_schema": json.dumps(required_schema(), ensure_ascii=False, indent=2),
        "sections_hint": (
            ""
            if timeline
            else "\n  **这条内容是文章，没有时间轴，`start`/`end` 留空即可**，`sections` 只用来分节。"
        ),
        "timecode_rule": (
            "时间码只用正文里出现过的值，格式 `mm:ss`（超过一小时用 `h:mm:ss`）。"
            if timeline
            else "这条内容是文章，没有时间轴，所有 `start`/`end` 字段留空或省略。"
        ),
        "chapters": chapters_block(bundle),
        "transcript_body": _transcript_body(parts),
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
    }

    template = resource_text("templates/brief.md")
    for key, value in values.items():
        template = template.replace("{{%s}}" % key, value)
    # 空占位符（比如正文没有切片、提示不适用）会留下连续空行，收一下
    template = re.sub(r"\n{3,}", "\n\n", template)
    return template if template.endswith("\n") else template + "\n"
