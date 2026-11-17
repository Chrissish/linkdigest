"""mlx-whisper 适配器（Apple Silicon，走 Metal）。

与 faster-whisper 的差异（上游能力差异，不是本适配器的缺陷，换引擎后症状不会自动消失）：
- 没有 VAD。mlx_whisper 0.4.x 不收 `vad_filter`，静音段仍要过编码器，
  只靠 `no_speech_threshold` 在解码层丢弃，长静音的播客会比 faster 慢。
- 不返回语言检测概率，`language_probability` 只能记 0.0（未知），不要填 1.0 假装确定。
- segment 上带 `avg_logprob`，换算方式与 faster 保持一致，否则换引擎后
  平均置信度、以及「平均置信度偏低」告警的阈值都不可比。

**不下载模型。** mlx_whisper 收到 HF 仓库名会自己去 `snapshot_download`，所以这里一律先解析成
本地目录再传给它；解析不到就抛错，让门面去决定换引擎，而不是在这里静默拉几个 GB。
"""

from __future__ import annotations

from pathlib import Path

from ...config import Settings
from .base import ASRResult, ASRSegment, avg_logprob_to_conf
from .pcm import load_pcm

MLX_MODELS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny-mlx",
}

# mlx_whisper.load_models.load_model 只认这两个权重文件，缺一个都加载不了
WEIGHT_FILES = ("weights.safetensors", "weights.npz")


def is_model_dir(path: Path) -> bool:
    """目录里是不是一整套可直接加载的 MLX 权重（config.json + 权重文件）。"""
    if not path.is_dir() or not (path / "config.json").is_file():
        return False
    return any((path / name).is_file() for name in WEIGHT_FILES)


def model_candidates(cfg: Settings) -> list[Path]:
    """按优先级列出可能放着这套权重的目录。只拼路径，不判断存在性。

    `ASR_MODEL` 直接写成目录时以它为准；否则在 `ASR_MODEL_DIR` 下找，
    仓库名末段（`whisper-large-v3-turbo`）和配置名（`large-v3-turbo`）两种命名都认——
    手工用 `hf download --local-dir` 下下来的通常是前者。
    """
    repo = MLX_MODELS.get(cfg.asr_model, cfg.asr_model)
    candidates = [Path(cfg.asr_model).expanduser()]
    if cfg.asr_model_dir is not None:
        root = Path(cfg.asr_model_dir).expanduser()
        candidates += [root / repo.rsplit("/", 1)[-1], root / cfg.asr_model]
    return candidates


def local_model_path(cfg: Settings) -> Path | None:
    """只查本地，不联网、不下载：ASR_MODEL → ASR_MODEL_DIR → HF 缓存。找不到返回 None。"""
    for candidate in model_candidates(cfg):
        if is_model_dir(candidate):
            return candidate

    repo = MLX_MODELS.get(cfg.asr_model, cfg.asr_model)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:  # 连 hub 都没装，就没有缓存可查
        return None
    try:
        # local_files_only：只在缓存里找，缺了直接抛，不产生任何网络请求
        return Path(snapshot_download(repo_id=repo, local_files_only=True))
    except Exception:
        return None


class MlxWhisperEngine:
    name = "mlx-whisper"

    def __init__(self, cfg: Settings) -> None:
        self.repo = MLX_MODELS.get(cfg.asr_model, cfg.asr_model)
        self.model_path = local_model_path(cfg)
        configured = Path(cfg.asr_model).expanduser()
        if self.repo == cfg.asr_model and configured.is_dir():
            # ASR_MODEL 直接写成绝对目录时，identity 用目录名：报告里不该出现随机器变的路径
            self.repo = configured.name

    @property
    def engine_id(self) -> str:
        # 记仓库名而不是缓存路径：路径随机器变，引擎 identity 不该跟着变
        return f"mlx-whisper:{self.repo}"

    def warmup(self) -> None:
        """真加载一次权重，失败即抛。

        加载成功后 mlx_whisper 的 ModelHolder 会缓存这份模型，
        transcribe 复用同一个实例，不会二次吃内存和时间。
        """
        if self.model_path is None:
            raise RuntimeError(f"本地没有 {self.repo} 的模型权重")
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.get_model(str(self.model_path), mx.float16)

    def transcribe(self, wav: Path, language: str | None, duration: float) -> ASRResult:
        if self.model_path is None:
            raise RuntimeError(
                f"本地没有 {self.repo} 的模型权重，本程序不代下模型；"
                "请先下载，或把 ASR_MODEL 指向本地模型目录"
            )

        import mlx_whisper

        # 喂 16k 单声道 float32 PCM。传路径会让 mlx_whisper 再起一个 ffmpeg 子进程重新解码，
        # 而 wav 已经是 ffmpeg.to_wav 的产物，那一步纯属重复劳动。
        result = mlx_whisper.transcribe(
            load_pcm(wav),
            path_or_hf_repo=str(self.model_path),
            language=language,
            condition_on_previous_text=False,
            word_timestamps=False,
            verbose=None,
        )
        segments = [
            ASRSegment(
                start=float(s["start"]),
                end=float(s["end"]),
                text=str(s["text"]).strip(),
                confidence=avg_logprob_to_conf(s.get("avg_logprob")),
            )
            for s in result.get("segments", [])
        ]
        last_end = segments[-1].end if segments else 0.0
        return ASRResult(
            segments=segments,
            language=(result.get("language") or language or "unknown"),
            language_probability=0.0,
            engine=self.engine_id,
            duration=max(float(duration or 0.0), last_end),
        )
