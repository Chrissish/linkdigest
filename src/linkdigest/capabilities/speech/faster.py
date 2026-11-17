"""faster-whisper 适配器（CPU / CUDA 通用）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import Settings
from .base import ASRResult, ASRSegment, avg_logprob_to_conf
from .pcm import load_pcm

_MODEL_CACHE: dict[str, Any] = {}


def _load_model(cfg: Settings):
    """加载模型：支持本地目录；网络不可用时退回本地缓存。

    国内网络直连 huggingface.co 常被代理拦成 502，但模型已在缓存里。
    所以先按常规加载，失败后强制本地模式再试一次，都失败才报错。
    """
    from faster_whisper import WhisperModel

    device = cfg.asr_device if cfg.asr_device != "auto" else "cpu"
    key = f"{cfg.asr_model}|{device}|{cfg.asr_compute_type}"
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    kwargs = {"device": device, "compute_type": cfg.asr_compute_type}
    local_dir = Path(cfg.asr_model)
    if local_dir.is_dir():
        _MODEL_CACHE[key] = WhisperModel(str(local_dir), **kwargs)
        return _MODEL_CACHE[key]

    try:
        _MODEL_CACHE[key] = WhisperModel(cfg.asr_model, **kwargs)
    except Exception as first:
        try:
            _MODEL_CACHE[key] = WhisperModel(cfg.asr_model, local_files_only=True, **kwargs)
        except Exception as second:
            raise RuntimeError(
                f"ASR 模型 {cfg.asr_model} 加载失败：{first}；离线重试也失败：{second}。"
                "国内网络请设置 HF_ENDPOINT=https://hf-mirror.com，或把 ASR_MODEL 指向本地模型目录。"
            ) from second
    return _MODEL_CACHE[key]


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg

    @property
    def engine_id(self) -> str:
        return f"faster-whisper:{self.cfg.asr_model}:{self.cfg.asr_compute_type}"

    def transcribe(self, wav: Path, language: str | None, duration: float) -> ASRResult:
        model = _load_model(self.cfg)
        iterator, info = model.transcribe(
            load_pcm(wav),
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
                confidence=avg_logprob_to_conf(getattr(seg, "avg_logprob", None)),
            )
            for seg in iterator
        ]
        return ASRResult(
            segments=segments,
            language=(info.language or language or "unknown"),
            language_probability=float(info.language_probability or 0.0),
            engine=self.engine_id,
            duration=float(info.duration or duration),
        )
