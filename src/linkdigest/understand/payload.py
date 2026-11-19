"""模型 JSON → Report。

这里只做「字段收口」：类型收敛、时间码解析、空值丢弃、别名兼容。
提示词的宽容度与这里的宽容度要保持一致，否则模型写对了却被丢掉。
"""

from __future__ import annotations

import ast
import json
import re

from ..contracts.models import DataPoint, Quote, Report, ReportSection
from ..conventions.timecode import parse_ts


def _line(value: object) -> str:
    """一句话字段的收口：模型偶尔把 {text,start} 对象塞进字符串数组，
    直接 str() 会渲染成 `{'text': ...}` 的原始字典——在这里拆成一句话。"""
    if isinstance(value, dict):
        text = str(value.get("text") or value.get("point") or "").strip()
        at = value.get("start") or value.get("at")
        if not text:
            return ""
        return f"{text}（{at}）" if at else text
    return str(value).strip()


def _prose(value: object, *, level: int = 3) -> str:
    """自由文本字段（appendix）的收口。

    模型有时把结构（dict / list）塞进本该是 Markdown 的字段，有时还把它压成
    字符串形式。直接 str() 落盘，页面就会吐出 `{'标题': [...]}` 这种字面量——
    这里统一压成 Markdown：字典键变三级标题，列表项变无序列表。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text[0] in "[{":  # 字符串化的 JSON / Python 字面量
            parsed: object = None
            for loader in (json.loads, ast.literal_eval):
                try:
                    parsed = loader(text)  # type: ignore[operator]
                    break
                except (ValueError, SyntaxError, TypeError):
                    continue
            if isinstance(parsed, (dict, list)):
                return _prose(parsed, level=level)
        return text
    if isinstance(value, dict):
        blocks: list[str] = []
        for key, val in value.items():
            head = str(key).strip()
            inner = _prose(val, level=level + 1)
            if head and inner:
                blocks.append(f"{'#' * level} {head}\n\n{inner}")
            elif head:
                blocks.append(f"{'#' * level} {head}")
            elif inner:
                blocks.append(inner)
        return "\n\n".join(blocks)
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for item in value:
            if isinstance(item, (dict, list, tuple)):
                inner = _prose(item, level=level)
                if inner:
                    lines.append("- " + inner.replace("\n", " "))
            else:
                text = str(item).strip()
                if text:
                    lines.append(f"- {text}")
        return "\n".join(lines)
    return str(value).strip()


def _squash(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text).lower()


def _is_body_repeat(appendix: str, body: str) -> bool:
    """附录是不是正文的重排：按字符二元组算覆盖率，七成以上重复就当重排丢掉。

    比逐行子串匹配稳，正文换个说法复述同一批事实也能识别出来。
    """
    a, b = _squash(appendix), _squash(body)
    if len(a) < 40 or not b:
        return False
    grams = {a[i : i + 2] for i in range(len(a) - 1)}
    hit = sum(1 for g in grams if g in b)
    return grams and hit / len(grams) >= 0.7


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
    appendix = _prose(payload.get("appendix") or payload.get("extra") or "")
    body = str(payload.get("body") or "")
    if _is_body_repeat(appendix, body):
        appendix = ""

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
        body=body,
        appendix=appendix,
        tldr=[str(x) for x in payload.get("tldr") or []],
        data_points=data_points,
        quotes=quotes,
        controversies=[s for s in (_line(x) for x in payload.get("controversies") or []) if s],
        open_questions=[s for s in (_line(x) for x in payload.get("open_questions") or []) if s],
        terms={str(k): str(v) for k, v in (payload.get("terms") or {}).items()},
    )
