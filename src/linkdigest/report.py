"""理解与报告：层级 map-reduce。

分块摘要（map）→ 汇总成报告（reduce）。跨语言在提示层解决，不设独立翻译阶段：
无论原文是英/日/中，最终报告一律以简体中文产出，金句保留原文并附意译。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import settings
from .llm import LLMClient, LLMError
from .models import DataPoint, Quote, Report, ReportSection, TranscriptBundle

MAP_SYSTEM = """你是内容分析引擎。任务：把带时间戳的语音转写片段压成结构化摘要。
硬性要求：
1. 只依据给定文本，不补充外部知识，不推测未出现的内容。
2. 无论原文是英文、日文还是中文，summary/points/terms 的解释一律用简体中文。
3. 时间码必须是输入中出现过的值，格式 mm:ss。
4. 只输出 JSON，不要解释。"""

MAP_USER = """片段范围：{start} - {end}
原文语言：{lang}

转写内容：
{body}

输出 JSON：
{{
  "summary": "这段在讲什么，2-3 句，中文",
  "points": ["论点或信息点，中文，每条不超过 40 字"],
  "data": [{{"label": "指标/数字含义", "value": "具体数值", "at": "mm:ss"}}],
  "quotes": [{{"text": "原文金句（保留原语言）", "zh": "中文意译", "at": "mm:ss"}}],
  "terms": {{"原文术语": "中文解释"}}
}}"""

REDUCE_SYSTEM = """你是内容研究员，负责把分段摘要整合成一份可信的内容总结报告。
要求：
1. 只用给定材料，不编造数据、人名、时间。
2. 报告语言：简体中文（金句可保留原文并附意译）。
3. 若材料中出现互相矛盾的说法，写进 controversies，不要替它下结论。
4. 时间码只用输入中出现过的。
5. 条数随内容走，没有配额，不要为凑数而写。
6. 只输出 JSON。"""

REDUCE_USER = """内容元信息：
标题：{title}
平台：{platform}
作者：{author}
时长/篇幅：{duration}
原语言：{lang}
字数：{chars}

分段摘要（按时间顺序）：
{digests}

机械章节切分（可调整合并，但时间范围要落在相邻章节内）：
{chapters}

输出 JSON：
{{
  "intent": "可选。读者是谁、要回答什么问题，一句话",
  "one_liner": "一句话说清这是什么、讲了什么",
  "tags": ["3-5 个内容标签词，名词或短语，供知识库归类"],
  "tldr": ["核心结论，条数与每条的详略都由内容决定"],
  "body": "可选。3-6 段正文，平直整理源内容：讲了什么、按什么顺序、给了哪些具体做法和判断。不做评价、不夹批注。不超过 800 字",
  "sections": [{{"title": "小标题", "start": 0, "end": 300, "points": ["要点1", "要点2"]}}],
  "key_points": ["最重要的信息，条数随内容"],
  "data_points": [{{"label": "指标", "value": "数值", "start": 0}}],
  "quotes": [{{"text": "原文", "start": 0, "note": "为什么值得记"}}],
  "controversies": ["有争议/未证实的说法"],
  "open_questions": ["材料没回答但重要的问题"],
  "terms": {{"术语": "解释"}},
  "appendix": "可选。通用字段装不下的结构（时间线/表格/代码），Markdown，自带 ### 标题"
}}"""


def ts(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_ts(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", value.strip())
        if m:
            h = int(m.group(1) or 0)
            return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None


@dataclass(slots=True)
class Chunk:
    start: float
    end: float
    body: str
    chars: int


def chunk_bundle(bundle: TranscriptBundle, max_chars: int | None = None) -> list[Chunk]:
    budget = max_chars or settings.llm_chunk_chars
    timeline = not any("para_index" in s.flags for s in bundle.segments[:1])
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


def _map_chunk(client: LLMClient, chunk: Chunk, lang: str) -> dict:
    try:
        return client.chat_json(
            MAP_SYSTEM,
            MAP_USER.format(start=ts(chunk.start), end=ts(chunk.end), lang=lang, body=chunk.body),
            max_tokens=1600,
        )
    except LLMError as exc:
        client.stats.errors.append(f"map {ts(chunk.start)}: {exc}")
        return {"summary": "", "points": [], "data": [], "quotes": [], "terms": {}}


def _format_digest(index: int, chunk: Chunk, digest: dict) -> str:
    points = "；".join(str(p) for p in (digest.get("points") or [])[:8])
    return (
        f"### 段 {index} [{ts(chunk.start)}-{ts(chunk.end)}]\n"
        f"{digest.get('summary', '')}\n"
        f"要点：{points}\n"
    )


def _chapters_block(bundle: TranscriptBundle) -> str:
    return "\n".join(
        f"- {c.title}: {ts(c.start)} - {ts(c.end)} (start={c.start:.0f}, end={c.end:.0f})"
        for c in bundle.chapters
    ) or "（无）"


def _to_report(payload: dict) -> Report:
    sections = []
    for item in payload.get("sections") or []:
        if not isinstance(item, dict):
            continue
        sections.append(
            ReportSection(
                title=str(item.get("title") or "未命名小节"),
                start=parse_ts(item.get("start")),
                end=parse_ts(item.get("end")),
                points=[str(p) for p in (item.get("points") or [])],
            )
        )
    quotes = [
        Quote(text=str(q.get("text", "")), start=parse_ts(q.get("start")), note=q.get("note"))
        for q in payload.get("quotes") or []
        if isinstance(q, dict) and q.get("text")
    ]
    data_points = [
        DataPoint(label=str(d.get("label", "")), value=str(d.get("value", "")), start=parse_ts(d.get("start")))
        for d in payload.get("data_points") or []
        if isinstance(d, dict) and d.get("label")
    ]
    # extra 是早期别名，与 appendix 同义；两者都给时以 appendix 为准
    appendix = payload.get("appendix") or payload.get("extra") or ""
    tags: list[str] = []
    for tag in payload.get("tags") or []:
        word = str(tag).strip()
        if word and word not in tags:
            tags.append(word)
    return Report(
        one_liner=str(payload.get("one_liner") or ""),
        tags=tags[:5],
        sections=sections,
        intent=str(payload.get("intent") or ""),
        body=str(payload.get("body") or ""),
        appendix=str(appendix),
        tldr=[str(x) for x in payload.get("tldr") or []],
        key_points=[str(x) for x in payload.get("key_points") or []],
        data_points=data_points,
        quotes=quotes,
        controversies=[str(x) for x in payload.get("controversies") or []],
        open_questions=[str(x) for x in payload.get("open_questions") or []],
        terms={str(k): str(v) for k, v in (payload.get("terms") or {}).items()},
    )


def build_report(bundle: TranscriptBundle, client: LLMClient | None = None) -> tuple[Report, dict]:
    client = client or LLMClient()
    chunks = chunk_bundle(bundle)
    if not client.available:
        return degraded_report(bundle), {"mode": "degraded", "chunks": len(chunks)}

    workers = min(4, max(1, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        digests = list(pool.map(lambda c: _map_chunk(client, c, bundle.language.detected), chunks))

    digest_text = "\n".join(_format_digest(i + 1, c, d) for i, (c, d) in enumerate(zip(chunks, digests)))
    duration = bundle.source.duration_sec or (bundle.segments[-1].end if bundle.segments else 0)
    user = REDUCE_USER.format(
        title=bundle.source.title or "未命名内容",
        platform=bundle.source.platform,
        author=bundle.source.author or "未知",
        duration=ts(duration) if duration else f"{bundle.quality.chars} 字",
        lang=bundle.language.detected,
        chars=bundle.quality.chars,
        digests=digest_text[: settings.llm_chunk_chars * 4],
        chapters=_chapters_block(bundle),
    )
    try:
        payload = client.chat_json(REDUCE_SYSTEM, user, max_tokens=6000)
    except LLMError as exc:
        client.stats.errors.append(f"reduce: {exc}")
        report = degraded_report(bundle)
        report.notes.append(f"汇总阶段调用失败（{exc}），已降级")
        return report, {"mode": "partial", "chunks": len(chunks)}

    report = _to_report(payload)
    report.notes.append(f"分块 {len(chunks)} 段，模型 {settings.llm_model}")
    return report, {"mode": "llm", "chunks": len(chunks)}


def _clip(text: str, limit: int) -> str:
    """按句子/词边界截断，别把单词切成 "st"、"th" 这种碎片。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    for sep in ("。", "！", "？", ". ", "! ", "? ", "；", "，", ", "):
        idx = window.rfind(sep)
        if idx >= int(limit * 0.6):
            return window[: idx + len(sep)].rstrip(" ,，")
    idx = window.rfind(" ")
    if idx >= int(limit * 0.6):
        return window[:idx]
    return window.rstrip() + "…"


def degraded_report(bundle: TranscriptBundle) -> Report:
    """无模型时的抽取式兜底：结构真、内容浅，明确标注降级。"""
    segs = bundle.segments
    if not segs:
        return Report(one_liner="没有可用文本", degraded=True, notes=["文本为空"])
    one_liner = _clip(segs[0].text, 60)
    sections: list[ReportSection] = []
    for chapter in bundle.chapters:
        inside = [s for s in segs if chapter.start <= s.start < chapter.end] or [segs[0]]
        sections.append(
            ReportSection(
                title=chapter.title,
                start=chapter.start,
                end=chapter.end,
                points=[_clip(s.text, 90) for s in inside[:3]],
            )
        )
    candidates = [s for s in segs if 20 <= len(s.text) <= 140]
    pool = candidates[3:] or candidates
    step = max(1, len(pool) // 6) if pool else 1
    key_points = [_clip(s.text, 140) for s in pool[::step][:6]]
    quotes = [
        Quote(text=_clip(s.text, 120), start=s.start)
        for s in segs
        if re.search(r"\d", s.text) and len(s.text) > 15
    ][:3]
    return Report(
        one_liner=one_liner,
        tldr=[_clip(s.text, 70) for s in segs[:3]],
        sections=sections,
        key_points=key_points,
        quotes=quotes,
        degraded=True,
        notes=["未配置 LLM_API_KEY，当前为抽取式降级摘要（无跨语言、无归纳）"],
    )


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


def _frontmatter(bundle: TranscriptBundle, report: Report, extra: dict) -> str:
    """标题、日期、来源、属性、标签进 frontmatter，Obsidian 等工具直接可读。"""
    src = bundle.source
    timeline = not any("para_index" in s.flags for s in bundle.segments[:1])
    duration = src.duration_sec or (bundle.segments[-1].end if bundle.segments else None)
    date = _local_stamp(extra.get("created_at"))
    mode = "degraded" if report.degraded else str(extra.get("mode") or "unknown")
    lines = ["---"]
    if src.title:
        lines.append(f"title: {_yq(src.title)}")
    lines.append(f"date: {_yq(date)}")
    url = src.canonical_url or src.url
    if url:
        lines.append(f"source: {_yq(url)}")
    if src.platform:
        lines.append(f"platform: {_yq(src.platform)}")
    if src.author:
        lines.append(f"author: {_yq(src.author)}")
    lines.append("type: linkdigest")
    lines.append(f"mode: {_yq(mode)}")
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
    timeline = not any("para_index" in s.flags for s in bundle.segments[:1])
    out: list[str] = []
    title = bundle.source.title or "未命名内容"
    out.append(_frontmatter(bundle, report, extra))
    out.append("")
    out.append(f"# {title}")
    out.append("")
    if report.degraded:
        out.append("> 降级模式：未调用模型，以下为抽取式结果，仅保证结构，不具备归纳与跨语言能力。")
        out.append("")

    if report.one_liner:
        out.append(f"**{report.one_liner}**")
        out.append("")

    if report.tldr:
        out.append("## 速览")
        out.extend(f"- {item}" for item in report.tldr)
        out.append("")

    if report.body:
        out.append("## 正文")
        out.append("")
        out.append(report.body.strip())
        out.append("")

    if report.sections:
        out.append("## 分段解读")
        for section in report.sections:
            span = f"{ts(section.start)}–{ts(section.end)}" if timeline and section.start is not None else ""
            out.append(f"### {section.title} {span}".rstrip())
            out.extend(f"- {p}" for p in section.points)
            out.append("")

    if report.key_points:
        out.append("## 关键信息")
        out.extend(f"{i}. {p}" for i, p in enumerate(report.key_points, 1))
        out.append("")

    if report.data_points:
        out.append("## 数据点")
        out.append("| 指标 | 数值 | 位置 |" if timeline else "| 指标 | 数值 |")
        out.append("| --- | --- | --- |" if timeline else "| --- | --- |")
        for d in report.data_points:
            out.append(f"| {d.label} | {d.value} | {ts(d.start)} |" if timeline else f"| {d.label} | {d.value} |")
        out.append("")

    if report.quotes:
        out.append("## 值得记的原话")
        for q in report.quotes:
            note = f"  \n_{q.note}_" if q.note else ""
            anchor = f"  ({ts(q.start)})" if timeline else ""
            out.append(f"> {q.text}{anchor}{note}")
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
            out.append("## 补充")
            out.append("")
        out.append(block)
        out.append("")

    out.append("---")
    out.append("")
    stats = extra.get("llm_stats") or {}
    footer = [
        f"清洗：{bundle.quality.segment_count} 段 / {bundle.quality.chars} 字",
        f"置信度：{bundle.quality.avg_confidence if bundle.quality.avg_confidence is not None else 'n/a'}",
    ]
    if bundle.quality.flags:
        footer.append("动作：" + ", ".join(f"{k}={v}" for k, v in bundle.quality.flags.items()))
    if stats:
        footer.append(f"模型调用：{stats.get('calls')} 次 / {stats.get('prompt_tokens')}+{stats.get('completion_tokens')} tokens")
    out.append("_" + " | ".join(footer) + "_")
    if bundle.quality.warnings:
        out.append("")
        out.append("局限与告警：")
        out.extend(f"- {w}" for w in bundle.quality.warnings)
    for note in report.notes:
        out.append(f"- {note}")
    return "\n".join(out) + "\n"


def save_report(job_id: str, bundle: TranscriptBundle, report: Report,
                extra: dict | None = None) -> tuple:
    """两种形态同源落盘：report.md 给终端与版本库，report.html 给人读。

    收在这里是为了让「加一种输出格式」只改一处——pipeline 与 handoff 都走它。
    """
    from .html_report import render_html
    from .store import job_path

    md_path = job_path(job_id, "report.md")
    md_path.write_text(render_markdown(bundle, report, extra), encoding="utf-8")
    html_path = job_path(job_id, "report.html")
    html_path.write_text(render_html(bundle, report, extra), encoding="utf-8")
    return md_path, html_path
