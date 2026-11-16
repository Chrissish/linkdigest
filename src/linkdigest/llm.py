"""OpenAI 兼容客户端。不绑定厂商：base_url + model 就是全部耦合点。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return round(
            self.prompt_tokens / 1e6 * settings.llm_price_in + self.completion_tokens / 1e6 * settings.llm_price_out,
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


class LLMClient:
    def __init__(self) -> None:
        self.stats = LLMStats()

    @property
    def available(self) -> bool:
        return settings.llm_ready

    def _post(self, payload: dict) -> dict:
        url = f"{settings.llm_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=settings.llm_timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
                if resp.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    time.sleep(2)
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                return resp.json()
            except (httpx.HTTPError, LLMError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
        raise LLMError(str(last_error))

    def chat(self, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 2400,
             temperature: float | None = None) -> str:
        if not self.available:
            raise LLMError("未配置 LLM_API_KEY")
        payload: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.time()
        data = self._post(payload)
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
