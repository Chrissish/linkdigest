"""ASR 端口：上层只认这里的结构与协议。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class ASRSegment:
    start: float
    end: float
    text: str
    confidence: float | None = None


def avg_logprob_to_conf(value: float | None) -> float | None:
    """把 avg_logprob（平均对数概率）换成 0-1 的近似置信度。

    两个引擎都在 segment 上给这个值，换算方式必须一致，否则换引擎后
    `quality.avg_confidence` 和低置信度告警的阈值就不再可比。缺失返回 None。
    """
    if value is None:
        return None
    return round(min(1.0, math.exp(value)), 3)


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
