"""mlx-whisper 适配器（Apple Silicon，走 Metal）。"""

from __future__ import annotations

from pathlib import Path

from ...config import Settings
from .base import ASRResult, ASRSegment

MLX_MODELS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}


class MlxWhisperEngine:
    name = "mlx-whisper"

    def __init__(self, cfg: Settings) -> None:
        self.repo = MLX_MODELS.get(cfg.asr_model, cfg.asr_model)

    @property
    def engine_id(self) -> str:
        return f"mlx-whisper:{self.repo}"

    def transcribe(self, wav: Path, language: str | None, duration: float) -> ASRResult:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            str(wav),
            path_or_hf_repo=self.repo,
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
            engine=self.engine_id,
            duration=duration,
        )
