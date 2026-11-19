"""运行配置：一个 pydantic 模型，构造即校验，之后只读。

取值全部来自环境变量或 `.env`，没有配置中心。`load()` 只在进程入口调一次，
拿到的是**不可变**对象；运行期要换参数就 `model_copy` 出新对象往下一层传。
没有模块级单例，也就没有「谁把全局改过」这类问题。

默认路径分两种形态：源码树里落在仓库的 `data/`，安装后落到操作系统标准目录，
判定见 `config.env`，这里只消费结果。

唯一的环境副作用（把代理写进 HTTP_PROXY）不在构造时做，见 `linkdigest.bootstrap`：
配置对象只描述「该用什么」，不替进程做决定。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from .. import __version__
from ..net.proxy import detect_system_proxy
from .env import (
    ENV_ANCHOR,
    ENV_FILE,
    PROJECT_ROOT,
    env,
    env_float,
    env_int,
    env_opt_float,
    env_opt_int,
    resolve_path,
    user_data_dir,
)
from .toolchain import find_ffmpeg, find_js_runtime

SYSTEM_PROXY = detect_system_proxy()

__all__ = ["ConfigError", "Settings", "load"]


class ConfigError(ValueError):
    """配置不合格。消息里直接说清哪个键、期望什么、实际是什么。"""


class Settings(BaseModel):
    """一次运行的完整配置。frozen：任何一层都改不了它，只能换一份新的。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 位置
    data_dir: Path
    ffmpeg: str | None = None
    js_runtime: str | None = None

    # 转写
    asr_engine: str = "auto"
    asr_model: str = "small"
    # 本地模型根目录：模型体积大，放在数据盘/外置盘比放项目里合理。
    # 只用来「查」，程序不会往里下载任何东西。
    asr_model_dir: Path | None = None
    asr_compute_type: str = "int8"
    asr_device: str = "auto"
    asr_max_seconds: int = Field(default=4 * 3600, ge=60)

    # 理解
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout: float = Field(default=120.0, gt=0)
    llm_chunk_chars: int = Field(default=6000, ge=500)
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    # 思考模式开关（DeepSeek 系）：None=不发该字段（跟厂商默认走），
    # True/False 显式开/关。带思考的模型会先烧推理 token，长转写分块下
    # max_tokens 容易不够；结构化摘要任务关掉又快又省。
    llm_thinking: bool | None = None
    llm_price_in: float = Field(default=0.15, ge=0)
    llm_price_out: float = Field(default=0.60, ge=0)
    # 上下文窗口（tokens）：None=按模型名从内置家族表推断（deepseek→64K、
    # claude→200K……未知家族回落 32K 保守值）。官方 API 不暴露窗口大小，
    # 名称也不编码，所以表 + 覆盖是能做到的全部；换不认识的模型时手填这个。
    llm_context_tokens: int | None = Field(default=None, ge=1024)
    # 估算字/token 比：None=按内容语种构成自动估算（CJK 与拉丁文加权）。
    # 只用于「单次直读塞不塞得下」的预算判断，不影响计费。
    llm_chars_per_token: float | None = Field(default=None, gt=0)
    # 理解阶段看门狗：整个 build_report 的硬上限。线程挂死（pool 丢工、锁丢失）
    # 时 httpx 的请求级超时救不了——没有任何在飞的请求可超时。到点判失败，可重试。
    understand_timeout: float = Field(default=1800.0, gt=0)

    # 采集
    fetch_timeout: float = Field(default=30.0, gt=0)
    max_download_mb: int = Field(default=2048, ge=1)
    cookies_from_browser: str | None = None
    cookies_file: Path | None = None

    # 出口
    proxy: str | None = None
    system_proxy: str | None = None

    # 服务
    # 同时在跑的任务数上限。瓶颈在 ASR：mlx-whisper 走 Metal，
    # 调大了多任务会抢 GPU，不会出错但互相拖慢。
    job_workers: int = Field(default=5, ge=1, le=32)

    pipeline_version: str = __version__

    @field_validator("asr_engine", "asr_model", "asr_compute_type", "asr_device", "llm_model")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text

    @field_validator("llm_base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not text.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL 必须形如 https://api.openai.com/v1")
        return text

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db.sqlite3"

    @property
    def cookies_path(self) -> Path:
        """自备 cookies 的默认位置（cookies_file 未指定时用）。"""
        return self.data_dir / "cookies.txt"

    @property
    def llm_ready(self) -> bool:
        return self.llm_api_key is not None

    def llm_key(self) -> str | None:
        """只有真正要发请求时才取出明文。"""
        return self.llm_api_key.get_secret_value() if self.llm_api_key is not None else None

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        """可落盘的配置快照：密钥打码，其余原样。写进 manifest.json 供事后复现。"""
        data = self.model_dump(mode="json")
        data["llm_api_key"] = "***" if self.llm_ready else None
        data["llm_configured"] = self.llm_ready
        data["resolved_from"] = {
            "env_file": str(ENV_FILE) if ENV_FILE else None,
            "project_root": str(PROJECT_ROOT) if PROJECT_ROOT else None,
        }
        return data


# pydantic 的报错是英文且带一大段 title，这里翻成「哪个键、期望什么」。
# 自定义校验器抛的 ValueError 本身就是中文，直接取用。
_HINTS = {
    "greater_than_equal": ("不能小于 {value}", "ge"),
    "less_than_equal": ("不能大于 {value}", "le"),
    "greater_than": ("要大于 {value}", "gt"),
    "less_than": ("要小于 {value}", "lt"),
    "missing": ("缺这一项", None),
    "extra_forbidden": ("不认识这个键", None),
    "string_type": ("要文本", None),
    "int_parsing": ("要整数", None),
    "int_type": ("要整数", None),
    "float_parsing": ("要数字", None),
    "float_type": ("要数字", None),
}


def format_errors(exc: ValidationError) -> str:
    """把 ValidationError 压成一行：`字段：原因；字段：原因`。"""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "配置"
        kind = err.get("type", "")
        context = err.get("ctx") or {}
        template, bound = _HINTS.get(kind, (None, None))
        if template:
            limit = context.get(bound, "")
            if isinstance(limit, float) and limit.is_integer():
                limit = int(limit)  # 2.0 写成 2，读起来像人话
            reason = template.format(value=limit)
        elif kind == "value_error" and context.get("error") is not None:
            reason = str(context["error"])
        else:
            reason = err.get("msg", "不合格")
        parts.append(f"{loc}：{reason}")
    return "；".join(parts) or "配置不合格"


def _data_dir() -> Path:
    raw = env("LINK_DIGEST_DATA")
    if raw:
        return resolve_path(raw, ENV_ANCHOR)
    if PROJECT_ROOT is not None:
        return PROJECT_ROOT / "data"
    return user_data_dir()


def _cookies_file() -> Path | None:
    raw = env("COOKIES_FILE")
    return resolve_path(raw, ENV_ANCHOR) if raw else None


def _asr_model_dir() -> Path | None:
    raw = env("ASR_MODEL_DIR")
    return resolve_path(raw, ENV_ANCHOR) if raw else None


def _api_key() -> SecretStr | None:
    raw = env("LLM_API_KEY")
    return SecretStr(raw) if raw else None


def _env_thinking() -> bool | None:
    """LLM_THINKING：1/true/yes=开，0/false/no=关，未设=跟厂商默认。"""
    raw = env("LLM_THINKING")
    if raw is None or not raw.strip():
        return None
    text = raw.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"LLM_THINKING 的值看不懂：{raw!r}（可用 1/0、true/false、on/off）")


def load() -> Settings:
    """从环境与 .env 组装一份配置。坏值直接抛 ConfigError，不静默回落。"""
    try:
        return Settings(
            data_dir=_data_dir(),
            ffmpeg=find_ffmpeg(),
            js_runtime=find_js_runtime(),
            asr_engine=(env("ASR_ENGINE", "auto") or "auto").lower(),
            asr_model=env("ASR_MODEL", "small") or "small",
            asr_model_dir=_asr_model_dir(),
            asr_compute_type=env("ASR_COMPUTE_TYPE", "int8") or "int8",
            asr_device=env("ASR_DEVICE", "auto") or "auto",
            asr_max_seconds=env_int("ASR_MAX_SECONDS", 4 * 3600),
            llm_base_url=env("LLM_BASE_URL", "https://api.openai.com/v1") or "",
            llm_api_key=_api_key(),
            llm_model=env("LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
            llm_timeout=env_float("LLM_TIMEOUT", 120.0),
            llm_chunk_chars=env_int("LLM_CHUNK_CHARS", 6000),
            llm_temperature=env_float("LLM_TEMPERATURE", 0.2),
            llm_thinking=_env_thinking(),
            llm_price_in=env_float("LLM_PRICE_IN", 0.15),
            llm_price_out=env_float("LLM_PRICE_OUT", 0.60),
            llm_context_tokens=env_opt_int("LLM_CONTEXT_TOKENS"),
            llm_chars_per_token=env_opt_float("LLM_CHARS_PER_TOKEN"),
            understand_timeout=env_float("UNDERSTAND_TIMEOUT", 1800.0),
            fetch_timeout=env_float("FETCH_TIMEOUT", 30.0),
            max_download_mb=env_int("MAX_DOWNLOAD_MB", 2048),
            cookies_from_browser=env("COOKIES_FROM_BROWSER"),
            cookies_file=_cookies_file(),
            proxy=_resolve_proxy(),
            system_proxy=SYSTEM_PROXY,
            job_workers=env_int("JOB_WORKERS", 5),
        )
    except ValidationError as exc:
        raise ConfigError("配置不合格：" + format_errors(exc)) from exc
    except ValueError as exc:
        raise ConfigError(f"配置不合格：{exc}") from exc


def _resolve_proxy() -> str | None:
    return (
        env("PROXY_URL")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
        or SYSTEM_PROXY
    )
