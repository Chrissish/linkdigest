"""理解层门面：拼请求、收 JSON、记账。

传输层可替换（默认为 OpenAI 兼容端点），门面只依赖传输的 `complete(payload)`。
没有密钥时 `available` 为 False，上层据此走降级/交接分支，而不是抛异常。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...config import Settings
from .base import JsonChat, LLMError
from .openai import OpenAICompatibleTransport
from .stats import LLMStats

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class LLMClient(JsonChat):
    def __init__(
        self,
        cfg: Settings,
        transport: Any | None = None,
        model: str | None = None,
        debug_log: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.transport = transport if transport is not None else OpenAICompatibleTransport(cfg)
        self.model = model or cfg.llm_model
        self.stats = LLMStats(price_in=cfg.llm_price_in, price_out=cfg.llm_price_out)
        # 每次调用的前后事件写进任务目录的 llm_debug.jsonl：排查「卡在哪一次调用」
        # 不用再猜。写失败静默——调试日志不能反过来弄坏主流程。
        self.debug_log: Path | None = debug_log
        # 最近一次响应的 finish_reason（length=截断），报错时能说清是预算不够还是格式跑偏
        self.last_finish_reason: str | None = None

    def _log(self, event: str, detail: str) -> None:
        if self.debug_log is None:
            return
        line = json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": event,
                "detail": detail,
            },
            ensure_ascii=False,
        )
        try:
            with open(self.debug_log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

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
        # 思考模式开关（DeepSeek 系）：None=不传、跟厂商默认；True/False 显式控制。
        # 文档：https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
        if self.cfg.llm_thinking is not None:
            payload["thinking"] = {"type": "enabled" if self.cfg.llm_thinking else "disabled"}
        started = time.time()
        self._log("request", f"model={self.model} max_tokens={max_tokens} json={json_mode}")
        try:
            data = self.transport.complete(payload)
        except Exception as exc:
            self._log("error", f"{type(exc).__name__}: {str(exc)[:200]}")
            raise
        self.stats.seconds += time.time() - started
        self.stats.calls += 1
        self.last_finish_reason = (data.get("choices") or [{}])[0].get("finish_reason")
        usage = data.get("usage") or {}
        self.stats.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.stats.completion_tokens += int(usage.get("completion_tokens") or 0)
        self._log(
            "response",
            f"finish={self.last_finish_reason} seconds={time.time() - started:.1f}"
            f" prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')}",
        )
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"响应结构异常: {str(data)[:300]}") from exc

    def chat_json(self, system: str, user: str, *, max_tokens: int = 2400) -> dict:
        """JSON 请求：解析失败自动重试，逐次放大输出预算。

        带思考的模型在 max_tokens 里先烧推理再吐正文，预算紧了 content 会
        被截断甚至为空——翻倍预算比换提示词管用。最多 3 次，不放大故障。
        """
        limit = max_tokens
        last_exc: LLMError | None = None
        for attempt in range(3):
            content = self.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                json_mode=True,
                max_tokens=limit,
            )
            try:
                return parse_json(content)
            except LLMError as exc:
                last_exc = exc
                if attempt == 2:
                    break
                limit = min(limit * 2, 16000)
        raise LLMError(
            f"连续 3 次未拿到可解析 JSON（finish_reason={self.last_finish_reason}，"
            f"输出预算已放到 {limit}）：{last_exc}"
        ) from last_exc


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
