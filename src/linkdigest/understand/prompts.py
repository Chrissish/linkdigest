"""提示词装载。

提示词是内容，不是代码：`resources/prompts/*.md` 里一行一处改，改完不用碰 Python。
文件内的 `{字段}` 是模板占位符，`{{ }}` 是字面花括号（JSON 示例用）。

产出体例（字段、去重、正文口径）不在 prompts 里维护——那在 `templates/spec.md`
这一份事实源里，handoff 作业单和这里的 reduce 提示词都从 `contracts.spec` 渲染。
"""

from __future__ import annotations

from functools import lru_cache

from ..contracts.models import TranscriptBundle
from ..contracts.spec import spec_text
from ..conventions.resources import text

ROLE = "你是内容研究员，负责把分段摘要整合成一份可信的内容总结报告。"
# 管线与 handoff 的差异只有交付方式：模型直接回 JSON，不落文件
DELIVERY = "按上面的契约输出 answer.json 的完整 JSON 内容：直接输出 JSON 本体，不写文件、不要解释、不要包裹代码围栏。"


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    return text(f"prompts/{name}.md").strip()


def map_system() -> str:
    return _load("map.system")


def map_user() -> str:
    return _load("map.user")


def reduce_system(bundle: TranscriptBundle) -> str:
    return f"{ROLE}\n\n{spec_text(bundle.has_timeline)}\n{DELIVERY}"


def reduce_user() -> str:
    return _load("reduce.user")
