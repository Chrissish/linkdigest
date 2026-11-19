"""环境变量、路径锚点与 .env 装载。

同一份代码要同时应付两种部署形态，这里的规则必须都成立：

- 源码树：从包目录向上找 `pyproject.toml`，找到即仓库根。`.env` 与默认 `data/`
  落在仓库里，行为和以前一致。
- 已安装（wheel / skill 宿主 / 桌面应用）：找不到标记文件，改用操作系统标准目录，
  绝不往 site-packages 里写产物。

`__file__` 只在「向上找标记文件」这一步用，且限定层数，不假设固定的包深度——
之前按 `parents[3]` 硬算，`pip install` 之后会指向 `lib/python3.x`，`.env` 直接读不到。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "linkdigest"
PACKAGE_DIR = Path(__file__).resolve().parent
_MARKER = "pyproject.toml"
_WARNED: set[tuple[str, str]] = set()


def find_project_root(start: Path | None = None, limit: int = 4) -> Path | None:
    """向上找带 pyproject.toml 的目录；安装态返回 None。"""
    current = (start or PACKAGE_DIR).resolve()
    for _ in range(limit):
        if (current / _MARKER).is_file():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


PROJECT_ROOT = find_project_root()


def user_config_dir() -> Path:
    """配置文件目录（人工编辑的地方）。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def user_data_dir() -> Path:
    """任务数据目录（工具自己写的地方）。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def _env_candidates() -> list[Path]:
    explicit = os.environ.get("LINK_DIGEST_ENV")
    if explicit:
        return [Path(explicit).expanduser()]
    out = [Path.cwd() / ".env"]
    if PROJECT_ROOT is not None:
        out.append(PROJECT_ROOT / ".env")
    out.append(user_config_dir() / ".env")
    return out


def load_env_file() -> Path | None:
    """按候选顺序取第一个存在的 .env；真实环境变量始终优先（override=False）。"""
    seen: set[Path] = set()
    for candidate in _env_candidates():
        path = candidate.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        load_dotenv(path, override=False)
        return path
    return None


ENV_FILE = load_env_file()
# 相对路径（含 LINK_DIGEST_DATA=./data）锚在 .env 所在目录，而不是当时的 cwd
ENV_ANCHOR = ENV_FILE.parent if ENV_FILE is not None else None


def resolve_path(value: str, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return ((base or Path.cwd()) / path).resolve()


def env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def _warn(key: str, raw: str, kind: str, default: object) -> None:
    """坏值只提示一次，随后回落默认——静默改写配置比报错更难查。"""
    token = (key, raw)
    if token in _WARNED:
        return
    _WARNED.add(token)
    print(
        f"[linkdigest] 配置 {key}={raw!r} 不是合法{kind}，已按默认值 {default!r} 处理。",
        file=sys.stderr,
    )


def env_int(key: str, default: int) -> int:
    raw = env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _warn(key, raw, "整数", default)
        return default


def env_float(key: str, default: float) -> float:
    raw = env(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _warn(key, raw, "小数", default)
        return default


def env_opt_int(key: str) -> int | None:
    """可选整数：不设返回 None（=让程序自己推断），坏值告警后也回落 None。"""
    raw = env(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        _warn(key, raw, "整数", None)
        return None


def env_opt_float(key: str) -> float | None:
    raw = env(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        _warn(key, raw, "小数", None)
        return None
