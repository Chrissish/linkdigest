"""ASR 门面：引擎发现、选择、派发。

两个引擎产出同一结构，上层无感。引擎与模型来自传进来的配置，门面本身不读全局。
新增引擎 = 写一个满足 SpeechEngine 的文件 + 在 _REGISTRY 里登记一行。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ...config import Settings
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


def resolve_engine(cfg: Settings) -> str:
    """把配置里的引擎名解析成实际能用的那个。

    显式指定但没装，直接报错——悄悄换成另一个引擎会让「换引擎后结果没变」
    这种问题查很久。
    """
    wanted = _ALIASES.get(cfg.asr_engine.lower(), cfg.asr_engine.lower())
    engines = available_engines()
    known = {name for name, _, _ in _REGISTRY}
    if wanted in ("", "auto"):
        if not engines:
            raise RuntimeError("未安装任何 ASR 引擎，请 pip install faster-whisper 或 mlx-whisper")
        return engines[0]
    if wanted not in known:
        raise RuntimeError(
            f"未知的 ASR 引擎 {cfg.asr_engine!r}，可选：auto / {', '.join(sorted(known))}"
        )
    if wanted not in engines:
        raise RuntimeError(
            f"引擎 {wanted} 未安装（当前可用：{', '.join(engines) or '无'}）。"
            f"装它，或把 ASR_ENGINE 改成 auto。"
        )
    return wanted


def build_engine(cfg: Settings, name: str | None = None) -> SpeechEngine:
    target = name or resolve_engine(cfg)
    for engine_name, _, factory in _REGISTRY:
        if engine_name == target:
            return factory(cfg)
    raise RuntimeError(f"未知的 ASR 引擎：{target}")


def transcribe(cfg: Settings, wav: Path, language: str | None = None, duration: float = 0.0) -> ASRResult:
    engine = build_engine(cfg)
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
