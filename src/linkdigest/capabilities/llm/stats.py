"""用量与成本记账。成本按配置里的单价估算，只用于自查，不用于结算。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMStats:
    # 单价来自配置，构造时注入；没给就按 0 记，不影响功能
    price_in: float = 0.0
    price_out: float = 0.0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return round(
            self.prompt_tokens / 1e6 * self.price_in + self.completion_tokens / 1e6 * self.price_out,
            4,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 2),
            "cost_usd": self.cost_usd,
            "errors": self.errors,
        }
