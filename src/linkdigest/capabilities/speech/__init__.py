"""ASR 门面：引擎发现、选择、派发。

两个引擎产出同一结构，上层无感。引擎和模型都可由环境变量切换。
新增引擎 = 写一个满足 SpeechEngine 的文件 + 在 _REGISTRY 里登记一行。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ...config import settings
from .base import ASRResult, ASRSegment, SpeechEngine
from .faster import FasterWhisperEngine
from .language import normalize_language
from .mlx import MlxWhisperEngine

_ALIASES = {
    "faster-whisper": "faster-whisper",
    "faster": "faster-whisper",
    "mlx": "mlx-whisper",
    "mlx-whisper": "mlx-whisper",
}

# 名字 → (模块名, 工厂)。按探测顺序排列，auto 取第一个可用的。
_REGISTRY: list[tuple[str, str, type]] = [
    ("faster-whisper", "faster_whisper", FasterWhisperEngine),
    ("mlx-whisper", "mlx_whisper", MlxWhisperEngine),
]


def available_engines() -> list[str]:
    return [name for name, module, _ in _REGISTRY if importlib.util.find_spec(module) is not None]


def resolve_engine() -> str:
    wanted = _ALIASES.get(settings.asr_engine, settings.asr_engine)
    engines = available_engines()
    if wanted in {name for name, _, _ in _REGISTRY}:
        return wanted
    if not engines:
        raise RuntimeError("未安装任何 ASR 引擎，请 pip install faster-whisper 或 mlx-whisper")
    return engines[0]


def build_engine(name: str | None = None) -> SpeechEngine:
    target = name or resolve_engine()
    for engine_name, _, factory in _REGISTRY:
        if engine_name == target:
            return factory()
    raise RuntimeError(f"未知的 ASR 引擎：{target}")


def transcribe(wav: Path, language: str | None = None, duration: float = 0.0) -> ASRResult:
    engine = build_engine()
    return engine.transcribe(wav, normalize_language(language), duration)


__all__ = [
    "ASRResult",
    "ASRSegment",
    "SpeechEngine",
    "available_engines",
    "build_engine",
    "normalize_language",
    "resolve_engine",
    "transcribe",
]
