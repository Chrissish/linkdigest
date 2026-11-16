"""ASR 端口：上层只认这里的结构与协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class ASRSegment:
    start: float
    end: float
    text: str
    confidence: float | None = None


@dataclass(slots=True)
class ASRResult:
    segments: list[ASRSegment] = field(default_factory=list)
    language: str = "unknown"
    language_probability: float = 0.0
    engine: str = "unknown"
    duration: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)


class SpeechEngine(Protocol):
    """一个 ASR 实现要满足的形状。engine_id 用于写进契约的 quality.asr_engine。"""

    engine_id: str

    def transcribe(self, wav: Path, language: str | None, duration: float) -> ASRResult: ...
