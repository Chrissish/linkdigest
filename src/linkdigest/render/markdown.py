"""Report → Markdown。终端、版本库、diff、agent 读取都用这个形态。

frontmatter 里的键是给 Obsidian / Logseq 这类工具读的，正文只留内容与锚点。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..contracts.models import Report, TranscriptBundle
from ..conventions.timecode import ts


def _yq(value: Any) -> str:
    """YAML 双引号安全转义。"""
    return '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')


def _local_stamp(value: Any) -> str:
    """任务时间按本地时区写，与目录名的时间戳一致；解析不了就原样返回。"""
    if not value:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def frontmatter(bundle: TranscriptBundle, report: Report, extra: dict) -> str:
    """标题、日期、来源、属性、标签进 frontmatter。"""
    src = bundle.source
    timeline = bundle.has_timeline
    duration = bundle.duration
    mode = "degraded" if report.degraded else str(extra.get("mode") or "unknown")

    lines = ["---"]
    if src.title:
        lines.append(f"title: {_yq(src.title)}")
    lines.append(f"date: {_yq(_local_stamp(extra.get('created_at')))}")
    url = src.canonical_url or src.url
    if url:
        lines.append(f"source: {_yq(url)}")
    if src.platform:
        lines.append(f"platform: {_yq(src.platform)}")
    if src.author:
        lines.append(f"author: {_yq(src.author)}")
    lines.append("type: linkdigest")
    lines.append(f"mode: {_yq(mode)}")
    if extra.get("path"):
        lines.append(f"path: {_yq(extra['path'])}")
    if timeline and duration:
        lines.append(f"duration: {_yq(ts(duration))}")
    else:
        lines.append(f"chars: {bundle.quality.chars}")
    lines.append(f"language: {_yq(bundle.language.detected)}")
    if report.tags:
        lines.append("tags:")
        lines.extend(f"  - {_yq(t)}" for t in report.tags[:5])
    if report.intent:
        lines.append(f"intent: {_yq(report.intent)}")
    if extra.get("job_id"):
        lines.append(f"job: {_yq(extra['job_id'])}")
    lines.append("---")
    return "\n".join(lines)


def render_markdown(bundle: TranscriptBundle, report: Report, extra: dict | None = None) -> str:
    extra = extra or {}
    timeline = bundle.has_timeline
    out: list[str] = [frontmatter(bundle, report, extra), "", f"# {bundle.source.title or '未命名内容'}", ""]

    if report.degraded:
        out.append("> 降级模式：未调用模型，以下为抽取式结果，仅保证结构，不具备归纳与跨语言能力。")
        out.append("")

    if report.one_liner:
        out.extend([f"**{report.one_liner}**", ""])

    if report.tldr:
        out.append("## 速览")
        out.extend(f"- {item}" for item in report.tldr)
        out.append("")

    if report.sections:
        # 定位层：一行一段，给时间码与位置，不再展开成第二个叙事
        out.append("## 分段索引")
        for section in report.sections:
            span = f"{ts(section.start)}–{ts(section.end)}" if timeline and section.start is not None else ""
            out.append(f"- {span} **{section.title}**".replace("-  **", "- **"))
        out.append("")

    if report.body:
        out.extend(["## 正文", "", report.body.strip(), ""])

    if report.quotes:
        out.append("## 值得记的原话")
        for quote in report.quotes:
            note = f"  \n_{quote.note}_" if quote.note else ""
            anchor = f"  ({ts(quote.start)})" if timeline else ""
            out.append(f"> {quote.text}{anchor}{note}")
            out.append("")

    if report.controversies:
        out.append("## 存疑与争议")
        out.extend(f"- {c}" for c in report.controversies)
        out.append("")

    if report.open_questions:
        out.append("## 未回答的问题")
        out.extend(f"- {q}" for q in report.open_questions)
        out.append("")

    if report.terms:
        out.append("## 术语")
        out.extend(f"- **{k}**：{v}" for k, v in list(report.terms.items())[:20])
        out.append("")

    if report.appendix:
        block = report.appendix.strip()
        # 附录自带 ### 小标题时仍要补一层 ## 容器，否则会挂到上一节下面
        if not re.match(r"#{1,2}\s", block):
            out.extend(["## 补充", ""])
        out.extend([block, ""])

    out.extend(["---", ""])
    stats = extra.get("llm_stats") or {}
    footer = [
        f"清洗：{bundle.quality.segment_count} 段 / {bundle.quality.chars} 字",
        f"置信度：{bundle.quality.avg_confidence if bundle.quality.avg_confidence is not None else 'n/a'}",
    ]
    if bundle.quality.flags:
        footer.append("动作：" + ", ".join(f"{k}={v}" for k, v in bundle.quality.flags.items()))
    if stats:
        footer.append(
            f"模型调用：{stats.get('calls')} 次 / {stats.get('prompt_tokens')}+{stats.get('completion_tokens')} tokens"
        )
    out.append("_" + " | ".join(footer) + "_")
    if bundle.quality.warnings:
        out.extend(["", "局限与告警："])
        out.extend(f"- {w}" for w in bundle.quality.warnings)
    for note in report.notes:
        out.append(f"- {note}")
    return "\n".join(out) + "\n"
