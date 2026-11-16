"""环境变量读取。只做取值与类型收敛，不判断业务含义。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# .env 是仓库内的默认来源；真实环境变量优先级更高，不会被覆盖。
load_dotenv(PROJECT_ROOT / ".env")


def env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)) or default)
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    try:
        return float(env(key, str(default)) or default)
    except ValueError:
        return default
