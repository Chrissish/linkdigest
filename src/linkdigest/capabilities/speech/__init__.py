"""ASR 门面：引擎发现、优先级、降级、派发。

两个引擎产出同一结构，上层无感。引擎与模型来自传进来的配置，门面本身不读全局。

优先级：Apple Silicon 上优先 mlx-whisper（走 Metal），其余情况走 faster-whisper。
`auto` 不是「第一个装了的」，而是依次验证 **架构 → 本地模型文件 → 真的能加载**，
任何一步不过就换下一个引擎，原因写进 `ASRResult.raw["engine_note"]` 并进报告的质量说明。

**门面从不下载模型。** 只认 `ASR_MODEL` 指向的本地目录、`ASR_MODEL_DIR` 下的对应子目录，
或 HF 缓存里已有的权重；都没有就当不可用，把下载留给用户（模型动辄 GB，静默下载比报错更糟）。
显式指定 `ASR_ENGINE=mlx-whisper` 时不降级——悄悄换引擎会让「换了引擎结果没变」查很久。

新增引擎 = 写一个满足 SpeechEngine 的文件 + 在 _REGISTRY 里登记一行。
"""

from __future__ import annotations

import importlib.util
import platform
import sys
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

# 名字 → (模块名, 工厂)。这是登记顺序，不是优先级；优先级见 _ordered_engines。
_REGISTRY: list[tuple[str, str, type]] = [
    ("faster-whisper", "faster_whisper", FasterWhisperEngine),
    ("mlx-whisper", "mlx_whisper", MlxWhisperEngine),
]


def available_engines() -> list[str]:
    return [name for name, module, _ in _REGISTRY if importlib.util.find_spec(module) is not None]


def mlx_supported() -> bool:
    """架构这一层能不能跑 mlx：必须是 Apple Silicon，且 mlx / mlx-whisper 都装上。"""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    return all(importlib.util.find_spec(module) is not None for module in ("mlx", "mlx_whisper"))


def _ordered_engines() -> list[str]:
    """可用引擎按优先级排：Apple Silicon 上 MLX（Metal）排在 faster-whisper（CPU）前面。"""
    names = available_engines()
    if "mlx-whisper" in names and mlx_supported():
        names.remove("mlx-whisper")
        names.insert(0, "mlx-whisper")
    return names


def resolve_engine(cfg: Settings) -> str:
    """把配置里的引擎名解析成候选名。

    这里只解析名字，不验证模型能不能用——那需要真加载，代价高，交给 select_engine。
    显式指定但没装，直接报错：悄悄换成另一个引擎会让「换引擎后结果没变」这种问题查很久。
    """
    wanted = _ALIASES.get(cfg.asr_engine.lower(), cfg.asr_engine.lower())
    engines = available_engines()
    known = {name for name, _, _ in _REGISTRY}
    if wanted in ("", "auto"):
        ordered = _ordered_engines()
        if not ordered:
            raise RuntimeError("未安装任何 ASR 引擎，请 pip install faster-whisper 或 mlx-whisper")
        return ordered[0]
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


def _mlx_probe(cfg: Settings) -> tuple[SpeechEngine | None, str]:
    """按「架构 → 本地模型文件 → 真加载一次」判断 mlx 能不能用；不可用就给出原因。"""
    if not mlx_supported():
        return None, "非 Apple Silicon，或没装 mlx / mlx-whisper"
    engine = MlxWhisperEngine(cfg)
    if engine.model_path is None:
        return None, (
            f"本地没有 {engine.repo} 的权重（程序不代下；先下载，或把 ASR_MODEL 指向本地目录）"
        )
    try:
        engine.warmup()
    except Exception as exc:  # 权重损坏、版本不兼容、内存不足都走这里
        return None, f"{engine.repo} 加载失败：{exc}"
    return engine, ""


def _is_explicit(cfg: Settings) -> bool:
    return _ALIASES.get(cfg.asr_engine.lower(), cfg.asr_engine.lower()) not in ("", "auto")


def preview_engine(cfg: Settings) -> tuple[str, str]:
    """状态页用的廉价判定：架构 + 本地模型文件，**不加载权重**。

    与 select_engine 只差最后一步 warmup：1.5GB 权重不该由一次 health 请求承担，
    而「文件在不在」已经足够回答「这一进程实际会用哪个引擎」。
    返回 (引擎名, 说明)；说明为空表示按首选跑。
    """
    target = resolve_engine(cfg)
    if target != "mlx-whisper":
        return target, ""
    engine = MlxWhisperEngine(cfg)
    if engine.model_path is not None:
        return target, ""
    reason = f"本地没有 {engine.repo} 的权重"
    if "faster-whisper" in available_engines():
        return "faster-whisper", f"{reason}，将回落到 faster-whisper"
    return target, reason


def select_engine(cfg: Settings, name: str | None = None) -> tuple[SpeechEngine, str]:
    """挑一个真能跑的引擎，返回 (引擎, 降级原因)。降级原因为空串表示按首选跑。

    只有 `auto` 会降级；显式指定引擎时失败就报错，让人看见。
    """
    target = name or resolve_engine(cfg)
    explicit = name is not None or _is_explicit(cfg)
    if target != "mlx-whisper":
        return build_engine(cfg, target), ""

    engine, reason = _mlx_probe(cfg)
    if engine is not None:
        return engine, ""
    if explicit:
        raise RuntimeError(
            f"ASR 引擎 mlx-whisper 不可用：{reason}。"
            "把 ASR_ENGINE 改成 auto 可以回落到 faster-whisper。"
        )
    if "faster-whisper" not in available_engines():
        raise RuntimeError(
            f"mlx-whisper 不可用（{reason}），且 faster-whisper 未安装，没有可回落的引擎"
        )
    return build_engine(cfg, "faster-whisper"), (
        f"mlx-whisper 不可用（{reason}），已回落到 faster-whisper"
    )


def transcribe(cfg: Settings, wav: Path, language: str | None = None, duration: float = 0.0) -> ASRResult:
    engine, note = select_engine(cfg)
    result = engine.transcribe(wav, normalize_language(language), duration)
    if note:
        result.raw["engine_note"] = note
    return result


__all__ = [
    "ASRResult",
    "ASRSegment",
    "SpeechEngine",
    "available_engines",
    "build_engine",
    "mlx_supported",
    "normalize_language",
    "preview_engine",
    "resolve_engine",
    "select_engine",
    "transcribe",
]
