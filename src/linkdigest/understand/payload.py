"""模型 JSON → Report。

这里只做「字段收口」：类型收敛、时间码解析、空值丢弃、别名兼容。
提示词的宽容度与这里的宽容度要保持一致，否则模型写对了却被丢掉。
"""

from __future__ import annotations

from ..contracts.models import DataPoint, Quote, Report, ReportSection
from ..conventions.timecode import parse_ts


def report_from_payload(payload: dict) -> Report:
    sections = [
        ReportSection(
            title=str(item.get("title") or "未命名小节"),
            start=parse_ts(item.get("start")),
            end=parse_ts(item.get("end")),
            points=[str(p) for p in (item.get("points") or [])],
        )
        for item in payload.get("sections") or []
        if isinstance(item, dict)
    ]
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
        data_points=data_points,
        quotes=quotes,
        controversies=[str(x) for x in payload.get("controversies") or []],
        open_questions=[str(x) for x in payload.get("open_questions") or []],
        terms={str(k): str(v) for k, v in (payload.get("terms") or {}).items()},
    )
