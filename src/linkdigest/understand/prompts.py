"""提示词装载。

提示词是内容，不是代码：`resources/prompts/*.md` 里一行一处改，改完不用碰 Python。
文件内的 `{字段}` 是模板占位符，`{{ }}` 是字面花括号（JSON 示例用）。
"""

from __future__ import annotations

from functools import lru_cache

from ..conventions.resources import text


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    return text(f"prompts/{name}.md").strip()


def map_system() -> str:
    return _load("map.system")


def map_user() -> str:
    return _load("map.user")


def reduce_system() -> str:
    return _load("reduce.system")


def reduce_user() -> str:
    return _load("reduce.user")
