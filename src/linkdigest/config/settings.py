"""运行配置快照。

所有值来自环境变量或 .env，无外部配置中心；构造时一次性定下来，运行期不再重读，
避免「服务跑着跑着出口变了」这类不可复现的问题。需要改配置就重启进程。

唯一的环境副作用（把代理写进 HTTP_PROXY）不在构造时做，见 `linkdigest.bootstrap`：
配置对象只描述「该用什么」，不替进程做决定。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..net.proxy import detect_system_proxy
from .env import PROJECT_ROOT, env, env_float, env_int
from .toolchain import find_ffmpeg, find_js_runtime

SYSTEM_PROXY = detect_system_proxy()


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(env("LINK_DIGEST_DATA", str(PROJECT_ROOT / "data"))))
    ffmpeg: str | None = field(default_factory=find_ffmpeg)
    js_runtime: str | None = field(default_factory=find_js_runtime)

    asr_engine: str = field(default_factory=lambda: (env("ASR_ENGINE", "auto") or "auto").lower())
    asr_model: str = field(default_factory=lambda: env("ASR_MODEL", "large-v3") or "large-v3")
    asr_compute_type: str = field(default_factory=lambda: env("ASR_COMPUTE_TYPE", "int8") or "int8")
    asr_device: str = field(default_factory=lambda: env("ASR_DEVICE", "auto") or "auto")
    asr_max_seconds: int = field(default_factory=lambda: env_int("ASR_MAX_SECONDS", 4 * 3600))

    llm_base_url: str = field(
        default_factory=lambda: (env("LLM_BASE_URL", "https://api.openai.com/v1") or "").rstrip("/")
    )
    llm_api_key: str | None = field(default_factory=lambda: env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: env("LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini")
    llm_timeout: float = field(default_factory=lambda: env_float("LLM_TIMEOUT", 120.0))
    llm_chunk_chars: int = field(default_factory=lambda: env_int("LLM_CHUNK_CHARS", 6000))
    llm_temperature: float = field(default_factory=lambda: env_float("LLM_TEMPERATURE", 0.2))
    llm_price_in: float = field(default_factory=lambda: env_float("LLM_PRICE_IN", 0.15))
    llm_price_out: float = field(default_factory=lambda: env_float("LLM_PRICE_OUT", 0.60))

    fetch_timeout: float = field(default_factory=lambda: env_float("FETCH_TIMEOUT", 30.0))
    max_download_mb: int = field(default_factory=lambda: env_int("MAX_DOWNLOAD_MB", 2048))
    cookies_from_browser: str | None = field(default_factory=lambda: env("COOKIES_FROM_BROWSER"))
    # 实际生效的出口代理：PROXY_URL > shell 环境 > macOS 系统代理
    proxy: str | None = field(default_factory=lambda: _resolve_proxy())
    system_proxy: str | None = field(default_factory=lambda: SYSTEM_PROXY)

    pipeline_version: str = "0.1.0"

    def __post_init__(self) -> None:
        # 相对路径按进程 cwd 解析成绝对路径，避免从别的目录启动时产物散落
        self.data_dir = self.data_dir.expanduser().resolve()

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db.sqlite3"

    @property
    def cookies_path(self) -> Path:
        return self.data_dir / "cookies.txt"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def llm_ready(self) -> bool:
        return bool(self.llm_api_key)


def _resolve_proxy() -> str | None:
    return (
        env("PROXY_URL")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or SYSTEM_PROXY
    )


settings = Settings()
