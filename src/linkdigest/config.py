"""运行配置。所有值可通过环境变量或 .env 覆盖，无外部配置中心。"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _apply_proxy(proxy: str | None) -> str | None:
    """PROXY_URL 一旦设置就强制覆盖 shell 里的代理。

    动机：服务常被别的进程（IDE、沙箱、脚本）带着自己的 HTTP_PROXY 启动，
    用户明明配了代理，实际出口却是另一个，排查时极易误判成「网络不稳」。
    """
    if not proxy:
        return None
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ[key] = proxy
    bypass = [h for h in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(",") if h]
    for host in ("127.0.0.1", "localhost", "::1"):
        if host not in bypass:
            bypass.append(host)
    os.environ["NO_PROXY"] = ",".join(bypass)
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    return proxy


def system_proxy() -> str | None:
    """读 macOS「系统代理」设置。

    命令行进程不会继承系统代理——浏览器能开、curl 也能开（curl 会读 scutil），
    但 Python 的 httpx/requests 只认环境变量。代理软件开「系统代理」而不导出
    环境变量时，就会出现「浏览器可用、程序不可用」的怪现象。这里补上这一环。
    """
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["scutil", "--proxy"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    fields: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    if fields.get("HTTPSEnable") != "1":
        return None
    host = fields.get("HTTPSProxy") or fields.get("HTTPProxy")
    port = fields.get("HTTPSPort") or fields.get("HTTPPort")
    if not host or not port:
        return None
    return f"http://{host}:{port}"


def _env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)) or default)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)) or default)
    except ValueError:
        return default


def _resolve_ffmpeg() -> str | None:
    candidates = [
        _env("FFMPEG_BIN"),
        shutil.which("ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        str(Path.home() / "Documents/Works/FFmpeg/ffmpeg"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _resolve_js_runtime() -> str | None:
    """YouTube 的 n 挑战要有 JS 运行时才能解。

    yt-dlp 只认 PATH 里的 deno/node/bun；而 WorkBuddy、nvm、fnm 这类环境常把
    node 放在不写进 PATH 的版本目录里，于是「装了 node 却没有运行时」。
    """
    candidates = [
        _env("JS_RUNTIME_PATH"),
        shutil.which("deno"),
        shutil.which("node"),
        "/opt/homebrew/bin/deno",
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
    ]
    for pattern in (
        "~/.workbuddy/binaries/node/versions/*/bin/node",
        "~/.nvm/versions/node/*/bin/node",
        "~/.volta/bin/node",
        "~/.local/share/fnm/node-versions/*/installation/bin/node",
    ):
        found = sorted(Path(p).expanduser() for p in glob.glob(str(Path(pattern).expanduser())))
        candidates.extend(str(p) for p in reversed(found))
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


ENV_PROXY = _apply_proxy(_env("PROXY_URL"))
SYSTEM_PROXY = system_proxy()


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("LINK_DIGEST_DATA", str(PROJECT_ROOT / "data"))))
    ffmpeg: str | None = field(default_factory=_resolve_ffmpeg)
    js_runtime: str | None = field(default_factory=_resolve_js_runtime)

    asr_engine: str = field(default_factory=lambda: (_env("ASR_ENGINE", "auto") or "auto").lower())
    asr_model: str = field(default_factory=lambda: _env("ASR_MODEL", "large-v3") or "large-v3")
    asr_compute_type: str = field(default_factory=lambda: _env("ASR_COMPUTE_TYPE", "int8") or "int8")
    asr_device: str = field(default_factory=lambda: _env("ASR_DEVICE", "auto") or "auto")
    asr_max_seconds: int = field(default_factory=lambda: _env_int("ASR_MAX_SECONDS", 4 * 3600))

    llm_base_url: str = field(default_factory=lambda: (_env("LLM_BASE_URL", "https://api.openai.com/v1") or "").rstrip("/"))
    llm_api_key: str | None = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini")
    llm_timeout: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT", 120.0))
    llm_chunk_chars: int = field(default_factory=lambda: _env_int("LLM_CHUNK_CHARS", 6000))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.2))
    llm_price_in: float = field(default_factory=lambda: _env_float("LLM_PRICE_IN", 0.15))
    llm_price_out: float = field(default_factory=lambda: _env_float("LLM_PRICE_OUT", 0.60))

    fetch_timeout: float = field(default_factory=lambda: _env_float("FETCH_TIMEOUT", 30.0))
    max_download_mb: int = field(default_factory=lambda: _env_int("MAX_DOWNLOAD_MB", 2048))
    cookies_from_browser: str | None = field(default_factory=lambda: _env("COOKIES_FROM_BROWSER"))
    # 实际生效的出口代理：PROXY_URL > shell 环境 > macOS 系统代理
    proxy: str | None = field(
        default_factory=lambda: ENV_PROXY
        or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
        or SYSTEM_PROXY
    )
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

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def llm_ready(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
