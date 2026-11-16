"""音轨能力：只做一件事 —— 抽音轨，转 16k 单声道 wav。不抽帧。"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

from ..config import Settings


class MediaError(RuntimeError):
    pass


def available(cfg: Settings) -> bool:
    return bool(cfg.ffmpeg)


def to_wav(cfg: Settings, src: Path, dst: Path, timeout: int = 1800) -> Path:
    if not cfg.ffmpeg:
        raise MediaError("找不到 ffmpeg，请安装或设置 FFMPEG_BIN")
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        cfg.ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-map",
        "0:a:0?",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
        stderr = (proc.stderr or "").strip().splitlines()
        raise MediaError(f"ffmpeg 抽取音轨失败：{stderr[-1] if stderr else proc.returncode}")
    return dst


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as fh:
        frames = fh.getnframes()
        rate = fh.getframerate()
    return frames / float(rate) if rate else 0.0


def guard_duration(cfg: Settings, path: Path) -> float:
    duration = wav_duration(path)
    if duration <= 0.5:
        raise MediaError("音轨为空或过短")
    if duration > cfg.asr_max_seconds:
        raise MediaError(
            f"音频 {duration / 60:.0f} 分钟超过上限 {cfg.asr_max_seconds / 60:.0f} 分钟，"
            "请先切分（ASR_MAX_SECONDS 可调）"
        )
    return duration
