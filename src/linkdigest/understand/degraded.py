"""无模型时的抽取式兜底：结构真、内容浅，明确标注降级。

它保证前端、导出、存储链路永远可测，也保证「拿得到文字就一定拿得到报告」。
"""

from __future__ import annotations

import re

from ..contracts.models import Quote, Report, ReportSection, TranscriptBundle
from ..conventions.text import clip


def degraded_report(bundle: TranscriptBundle) -> Report:
    segs = bundle.segments
    if not segs:
        return Report(one_liner="没有可用文本", degraded=True, notes=["文本为空"])

    sections: list[ReportSection] = []
    for chapter in bundle.chapters:
        inside = [s for s in segs if chapter.start <= s.start < chapter.end] or [segs[0]]
        sections.append(
            ReportSection(
                title=chapter.title,
                start=chapter.start,
                end=chapter.end,
                points=[clip(s.text, 90) for s in inside[:3]],
            )
        )

    quotes = [
        Quote(text=clip(s.text, 120), start=s.start)
        for s in segs
        if re.search(r"\d", s.text) and len(s.text) > 15
    ][:3]

    return Report(
        one_liner=clip(segs[0].text, 60),
        tldr=[clip(s.text, 70) for s in segs[:3]],
        sections=sections,
        quotes=quotes,
        degraded=True,
        notes=["未配置 LLM_API_KEY，当前为抽取式降级摘要（无跨语言、无归纳）"],
    )
