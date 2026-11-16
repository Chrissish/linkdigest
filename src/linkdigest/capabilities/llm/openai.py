"""OpenAI 兼容的 `/chat/completions` 传输。

换厂商只需要换 base_url + model + key；真要换协议（比如 Anthropic 原生、
本地 llama.cpp server 的自定义路由），加一个提供同样 `complete()` 的传输类即可。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ...config import Settings
from .base import LLMError

RETRYABLE = {429, 500, 502, 503, 504}


class OpenAICompatibleTransport:
    def __init__(
        self,
        cfg: Settings,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or cfg.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else cfg.llm_key()
        self.timeout = cfg.llm_timeout if timeout is None else timeout

    @property
    def url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def complete(self, payload: dict[str, Any]) -> dict:
        """一次请求，遇到限流/网关错误重试一次（只一次，不放大故障）。"""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(self.url, headers=self.headers, json=payload)
                if response.status_code in RETRYABLE and attempt == 0:
                    time.sleep(2)
                    continue
                if response.status_code >= 400:
                    raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
                return response.json()
            except (httpx.HTTPError, LLMError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(2)
                    continue
        raise LLMError(str(last_error))
