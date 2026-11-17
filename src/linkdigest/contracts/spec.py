"""报告产出契约的渲染：单一事实源。

规范原文在 `resources/templates/spec.md`。handoff 作业单（handoff/brief.py）
和管线理解提示词（understand/prompts.py）都从这份模板渲染各自的交付物——
体例规则只允许改 spec.md，不许在任何一边另写一份。

占位符：`{{required_schema}}` 必填五件套（answer.template.json）、
`{{sections_hint}}` / `{{timecode_rule}}` 按「有无时间轴」换口径。
"""

from __future__ import annotations

import json
import re

from ..conventions.resources import text as resource_text


def required_schema() -> dict:
    """必填五件套。模板只放这些——塞满字段会鼓励凑数。"""
    return json.loads(resource_text("templates/answer.template.json"))


def spec_text(has_timeline: bool) -> str:
    """渲染契约正文。handoff 塞进 BRIEF.md 第 3 节，管线塞进 reduce 系统提示。"""
    values = {
        "required_schema": json.dumps(required_schema(), ensure_ascii=False, indent=2),
        "sections_hint": (
            ""
            if has_timeline
            else "\n  **这条内容是文章，没有时间轴，`start`/`end` 留空即可**，`sections` 只用来分节。"
        ),
        "timecode_rule": (
            "时间码只用正文里出现过的值，格式 `mm:ss`（超过一小时用 `h:mm:ss`）。"
            if has_timeline
            else "这条内容是文章，没有时间轴，所有 `start`/`end` 字段留空或省略。"
        ),
    }
    template = resource_text("templates/spec.md")
    for key, value in values.items():
        template = template.replace("{{%s}}" % key, value)
    # 空占位符（sections_hint 不适用时）会留下连续空行，收一下
    template = re.sub(r"\n{3,}", "\n\n", template)
    return template if template.endswith("\n") else template + "\n"
