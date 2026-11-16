"""理解层端口：上层只要求「能按 JSON 契约回答」，不关心是哪家模型。"""

from __future__ import annotations

from typing import Protocol

from .stats import LLMStats


class LLMError(RuntimeError):
    pass


class JsonChat(Protocol):
    """理解层需要的全部能力。实现可以是远程 API、本地模型，或测试里的假对象。"""

    stats: LLMStats

    @property
    def available(self) -> bool: ...

    def chat_json(self, system: str, user: str, *, max_tokens: int = 2400) -> dict: ...
