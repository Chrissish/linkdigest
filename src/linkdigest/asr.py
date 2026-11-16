"""ASR 适配层：faster-whisper 优先，mlx-whisper（Apple Silicon）兜底。

两个引擎产出同一结构，上层无感。引擎和模型都可由环境变量切换。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import settings

MLX_MODELS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}

_MODEL_CACHE: dict[str, Any] = {}


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


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def available_engines() -> list[str]:
    engines = []
    if _has("faster_whisper"):
        engines.append("faster-whisper")
    if _has("mlx_whisper"):
        engines.append("mlx-whisper")
    return engines


def resolve_engine() -> str:
    wanted = settings.asr_engine
    engines = available_engines()
    if wanted in {"faster-whisper", "faster"}:
        return "faster-whisper"
    if wanted in {"mlx", "mlx-whisper"}:
        return "mlx-whisper"
    if not engines:
        raise RuntimeError("未安装任何 ASR 引擎，请 pip install faster-whisper 或 mlx-whisper")
    return engines[0]


WHISPER_LANGS = set(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw "
    "he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my "
    "ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt "
    "uk ur uz vi yi yo zh yue".split()
)


def normalize_language(hint: str | None) -> str | None:
    """把平台给的语言标签压成 Whisper 认的两位码。

    YouTube 给的是 `en-US` / `zh-Hans-CN` 这种 BCP-47 标签，直接传给
    faster-whisper 会抛 "'en-US' is not a valid language code"，整条任务失败。
    压不成已知代码就返回 None，交给模型自己检测——比猜错更安全。
    """
    if not hint:
        return None
    code = hint.strip().replace("_", "-").split("-")[0].lower()
    return code if code in WHISPER_LANGS else None


def transcribe(wav: Path, language: str | None = None, duration: float = 0.0) -> ASRResult:
    engine = resolve_engine()
    language = normalize_language(language)
    if engine == "faster-whisper":
        return _faster(wav, language, duration)
    return _mlx(wav, language, duration)


def _load_pcm(path: Path):
    """自己读 PCM 喂给模型，绕开 PyAV。

    faster-whisper 1.2.x 调用 av.open(..., metadata_errors='ignore')，而 PyAV 19 已移除该参数，
    直接传文件路径会 TypeError。传 ndarray 则不经过解码器，版本漂移也不影响我们。
    """
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as fh:
        if fh.getnchannels() != 1 or fh.getsampwidth() != 2:
            raise RuntimeError("ASR 输入必须是 16-bit 单声道 wav（由 media.to_wav 保证）")
        frames = fh.readframes(fh.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0


def _load_model():
    """加载模型：支持本地目录；网络不可用时退回本地缓存。

    国内网络直连 huggingface.co 常被代理拦成 502，但模型已在缓存里。
    所以先按常规加载，失败后强制本地模式再试一次，都失败才报错。
    """
    from faster_whisper import WhisperModel

    device = settings.asr_device if settings.asr_device != "auto" else "cpu"
    local_dir = Path(settings.asr_model)
    kwargs = {"device": device, "compute_type": settings.asr_compute_type}

    key = f"{settings.asr_model}|{device}|{settings.asr_compute_type}"
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    if local_dir.is_dir():
        _MODEL_CACHE[key] = WhisperModel(str(local_dir), **kwargs)
        return _MODEL_CACHE[key]

    try:
        _MODEL_CACHE[key] = WhisperModel(settings.asr_model, **kwargs)
        return _MODEL_CACHE[key]
    except Exception as first:
        try:
            _MODEL_CACHE[key] = WhisperModel(settings.asr_model, local_files_only=True, **kwargs)
            return _MODEL_CACHE[key]
        except Exception as second:
            raise RuntimeError(
                f"ASR 模型 {settings.asr_model} 加载失败：{first}；离线重试也失败：{second}。"
                "国内网络请设置 HF_ENDPOINT=https://hf-mirror.com，或把 ASR_MODEL 指向本地模型目录。"
            ) from second


def _faster(wav: Path, language: str | None, duration: float) -> ASRResult:
    model = _load_model()
    iterator, info = model.transcribe(
        _load_pcm(wav),
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        word_timestamps=False,
    )
    segments = [
        ASRSegment(
            start=float(seg.start),
            end=float(seg.end),
            text=seg.text.strip(),
            confidence=_avg_logprob_to_conf(getattr(seg, "avg_logprob", None)),
        )
        for seg in iterator
    ]
    return ASRResult(
        segments=segments,
        language=(info.language or language or "unknown"),
        language_probability=float(info.language_probability or 0.0),
        engine=f"faster-whisper:{settings.asr_model}:{settings.asr_compute_type}",
        duration=float(info.duration or duration),
    )


def _mlx(wav: Path, language: str | None, duration: float) -> ASRResult:
    import mlx_whisper

    repo = MLX_MODELS.get(settings.asr_model, settings.asr_model)
    result = mlx_whisper.transcribe(
        str(wav),
        path_or_hf_repo=repo,
        language=language,
        condition_on_previous_text=False,
        word_timestamps=False,
        verbose=None,
    )
    segments = [
        ASRSegment(start=float(s["start"]), end=float(s["end"]), text=str(s["text"]).strip())
        for s in result.get("segments", [])
    ]
    return ASRResult(
        segments=segments,
        language=result.get("language") or language or "unknown",
        language_probability=1.0,
        engine=f"mlx-whisper:{repo}",
        duration=duration,
    )


def _avg_logprob_to_conf(value: float | None) -> float | None:
    if value is None:
        return None
    import math

    return round(min(1.0, math.exp(value)), 3)
