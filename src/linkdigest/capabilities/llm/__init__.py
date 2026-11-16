"""理解层门面：拼请求、收 JSON、记账。

传输层可替换（默认为 OpenAI 兼容端点），门面只依赖传输的 `complete(payload)`。
没有密钥时 `available` 为 False，上层据此走降级/交接分支，而不是抛异常。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ...config import Settings
from .base import JsonChat, LLMError
from .openai import OpenAICompatibleTransport
from .stats import LLMStats

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class LLMClient(JsonChat):
    def __init__(self, cfg: Settings, transport: Any | None = None, model: str | None = None) -> None:
        self.cfg = cfg
        self.transport = transport if transport is not None else OpenAICompatibleTransport(cfg)
        self.model = model or cfg.llm_model
        self.stats = LLMStats(price_in=cfg.llm_price_in, price_out=cfg.llm_price_out)

    @property
    def available(self) -> bool:
        return bool(getattr(self.transport, "api_key", None))

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        max_tokens: int = 2400,
        temperature: float | None = None,
    ) -> str:
        if not self.available:
            raise LLMError("未配置 LLM_API_KEY")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.cfg.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.time()
        data = self.transport.complete(payload)
        self.stats.seconds += time.time() - started
        self.stats.calls += 1
        usage = data.get("usage") or {}
        self.stats.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.stats.completion_tokens += int(usage.get("completion_tokens") or 0)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"响应结构异常: {str(data)[:300]}") from exc

    def chat_json(self, system: str, user: str, *, max_tokens: int = 2400) -> dict:
        content = self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_mode=True,
            max_tokens=max_tokens,
        )
        return parse_json(content)


def parse_json(content: str) -> dict:
    text = FENCE_RE.sub("", content).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"模型未返回合法 JSON: {exc}") from exc
    raise LLMError("模型未返回 JSON")


__all__ = ["LLMClient", "LLMError", "LLMStats", "JsonChat", "OpenAICompatibleTransport", "parse_json"]
