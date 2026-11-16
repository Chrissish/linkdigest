"""外部可执行体的定位：ffmpeg 与 JS 运行时。

命令行进程的 PATH 往往不等于用户实际安装的位置（Homebrew、nvm、WorkBuddy 自带的
版本目录都不一定在 PATH 里），所以这里按「显式配置 → PATH → 常见安装路径 → 版本管理器」
逐级找，只返回路径，不校验可用性。
"""

from __future__ import annotations

import glob
import shutil
from pathlib import Path

from .env import env


def find_ffmpeg() -> str | None:
    candidates = [
        env("FFMPEG_BIN"),
        shutil.which("ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        str(Path.home() / "Documents/Works/FFmpeg/ffmpeg"),
    ]
    return _first_existing(candidates)


def find_js_runtime() -> str | None:
    """YouTube 的 n 挑战要有 JS 运行时才能解。

    yt-dlp 只认 PATH 里的 deno/node/bun；而 WorkBuddy、nvm、fnv 这类环境常把
    node 放在不写进 PATH 的版本目录里，于是「装了 node 却没有运行时」。
    """
    candidates = [
        env("JS_RUNTIME_PATH"),
        shutil.which("deno"),
        shutil.which("node"),
        "/opt/homebrew/bin/deno",
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
    ]
    patterns = (
        "~/.workbuddy/binaries/node/versions/*/bin/node",
        "~/.nvm/versions/node/*/bin/node",
        "~/.volta/bin/node",
        "~/.local/share/fnm/node-versions/*/installation/bin/node",
    )
    for pattern in patterns:
        found = sorted(Path(p).expanduser() for p in glob.glob(str(Path(pattern).expanduser())))
        candidates.extend(str(p) for p in reversed(found))
    return _first_existing(candidates)


def _first_existing(candidates: list[str | None]) -> str | None:
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None
