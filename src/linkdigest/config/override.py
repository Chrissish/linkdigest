"""运行期覆盖：把 `asr.model=large-v3` 这类键值对套到配置上，得到**一份新配置**。

白名单制。只允许改行为参数；`data_dir` / `proxy` / `ffmpeg` / `js_runtime` 属于部署决策，
不进白名单——这几项一旦能从命令行参数或 HTTP 请求里改，等于把文件系统写入位置和出口
交给调用方。

输入两种写法都收：点号键（`asr.model`）与环境变量名（`ASR_MODEL`），后者方便直接照抄 .env。

    python -m linkdigest.cli <url> --set asr.model=large-v3 --set llm.temperature=0.4
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import SecretStr, ValidationError

from .settings import ConfigError, Settings, format_errors


def _text(raw: str) -> str:
    return raw.strip()


def _int(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"要整数，拿到 {raw!r}") from exc


def _float(raw: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"要数字，拿到 {raw!r}") from exc


def _path(raw: str) -> Path | None:
    text = raw.strip()
    return Path(text).expanduser() if text else None


def _secret(raw: str) -> SecretStr | None:
    text = raw.strip()
    return SecretStr(text) if text else None


def _optional(raw: str) -> str | None:
    text = raw.strip()
    return text or None


# 键 → (模型字段, 转换器)。只列白名单内的。
_FIELDS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "asr.engine": ("asr_engine", _text),
    "asr.model": ("asr_model", _text),
    "asr.model_dir": ("asr_model_dir", _path),
    "asr.compute_type": ("asr_compute_type", _text),
    "asr.device": ("asr_device", _text),
    "asr.max_seconds": ("asr_max_seconds", _int),
    "llm.base_url": ("llm_base_url", _text),
    "llm.api_key": ("llm_api_key", _secret),
    "llm.model": ("llm_model", _text),
    "llm.timeout": ("llm_timeout", _float),
    "llm.chunk_chars": ("llm_chunk_chars", _int),
    "llm.temperature": ("llm_temperature", _float),
    "fetch.timeout": ("fetch_timeout", _float),
    "max_download_mb": ("max_download_mb", _int),
    "cookies.from_browser": ("cookies_from_browser", _optional),
    "cookies.file": ("cookies_file", _path),
}

_ENV_ALIASES = {
    "ASR_ENGINE": "asr.engine",
    "ASR_MODEL": "asr.model",
    "ASR_MODEL_DIR": "asr.model_dir",
    "ASR_COMPUTE_TYPE": "asr.compute_type",
    "ASR_DEVICE": "asr.device",
    "ASR_MAX_SECONDS": "asr.max_seconds",
    "LLM_BASE_URL": "llm.base_url",
    "LLM_API_KEY": "llm.api_key",
    "LLM_MODEL": "llm.model",
    "LLM_TIMEOUT": "llm.timeout",
    "LLM_CHUNK_CHARS": "llm.chunk_chars",
    "LLM_TEMPERATURE": "llm.temperature",
    "FETCH_TIMEOUT": "fetch.timeout",
    "MAX_DOWNLOAD_MB": "max_download_mb",
    "COOKIES_FROM_BROWSER": "cookies.from_browser",
    "COOKIES_FILE": "cookies.file",
}

SETTABLE = tuple(sorted(_FIELDS))


def _normalize(key: str) -> str:
    name = key.strip()
    if name in _FIELDS:
        return name
    upper = name.upper()
    if upper in _ENV_ALIASES:
        return _ENV_ALIASES[upper]
    raise ConfigError(
        f"不支持覆盖 {name!r}。可改的键：{', '.join(SETTABLE)}"
        "（数据目录、代理、外部程序路径属于部署配置，只能在 .env 里改）"
    )


def parse(pairs: Iterable[str]) -> dict[str, Any]:
    """`["asr.model=large-v3", ...]` → `{"asr_model": "large-v3", ...}`。"""
    changes: dict[str, Any] = {}
    for item in pairs:
        if "=" not in item:
            raise ConfigError(f"覆盖要写成 key=value，拿到 {item!r}")
        key, _, value = item.partition("=")
        field, convert = _FIELDS[_normalize(key)]
        changes[field] = convert(value)
    return changes


def apply(base: Settings, pairs: Iterable[str]) -> Settings:
    """在 base 之上套一层覆盖，返回新对象（base 不变）。"""
    changes = parse(pairs)
    if not changes:
        return base
    merged = base.model_dump()
    merged.update(changes)
    try:
        return Settings(**merged)
    except ValidationError as exc:
        raise ConfigError("覆盖后的配置不合格：" + format_errors(exc)) from exc
