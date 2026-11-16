"""非代码资源的唯一入口：提示词、模板、报错文案、网页。

资源放在 `linkdigest/resources/`，随包分发（pyproject 的 package-data 负责），
代码里不再出现大段中文散文或 HTML。
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

_ROOT = "resources"


@lru_cache(maxsize=None)
def _read(relative: str) -> str:
    return files("linkdigest").joinpath(_ROOT, relative).read_text(encoding="utf-8")


def text(relative: str) -> str:
    """读一个文本资源，相对 `resources/`，结果缓存。"""
    return _read(relative)


def json_resource(relative: str) -> Any:
    return json.loads(_read(relative))
