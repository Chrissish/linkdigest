"""出口代理：读、选、写。

只碰环境变量和系统设置，不认识 settings，也不出网——这样配置层可以安全地依赖它。
"""

from __future__ import annotations

import os
import subprocess
import sys

PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_READ_ORDER = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy")
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def current() -> str | None:
    """当前进程实际会用的代理（每次现读环境，避免被启动时的快照骗到）。"""
    for key in _READ_ORDER:
        value = os.environ.get(key)
        if value:
            return value
    return None


def detect_system_proxy() -> str | None:
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


def normalize(value: str) -> str:
    value = value.strip()
    return value if "://" in value else f"http://{value}"


def apply(value: str) -> None:
    """把出口钉死到环境变量，localhost 永远直连。"""
    for key in PROXY_KEYS:
        os.environ[key] = value
    bypass = [h for h in (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "").split(",") if h]
    for host in _LOCAL_HOSTS:
        if host not in bypass:
            bypass.append(host)
    os.environ["NO_PROXY"] = ",".join(bypass)
    os.environ["no_proxy"] = os.environ["NO_PROXY"]


def clear() -> None:
    for key in PROXY_KEYS:
        os.environ.pop(key, None)


def candidates(system_proxy: str | None) -> list[str]:
    """所有值得一试的出口：环境变量里的（可能已失效）+ 系统代理。"""
    out: list[str] = []
    for raw in (current(), system_proxy):
        if not raw:
            continue
        value = normalize(raw)
        if value not in out:
            out.append(value)
    return out
