"""把 16-bit 单声道 wav 读成 float32 PCM。

自己读、不走 PyAV：faster-whisper 1.2.x 调用 av.open(..., metadata_errors='ignore')，
而 PyAV 19 已移除该参数，直接传文件路径会 TypeError。传 ndarray 则绕开解码器，
版本漂移也不影响我们。输入格式由 capabilities.ffmpeg.to_wav 保证。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def load_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as fh:
        if fh.getnchannels() != 1 or fh.getsampwidth() != 2:
            raise RuntimeError("ASR 输入必须是 16-bit 单声道 wav（由 capabilities.ffmpeg.to_wav 保证）")
        frames = fh.readframes(fh.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0
